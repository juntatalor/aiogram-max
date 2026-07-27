"""MaxSession — транспорт aiogram, говорящий с MAX Bot API.

Весь исходящий трафик aiogram проходит через ``BaseSession.make_request``:
Bot собирает типизированный объект метода (``SendMessage``, ``GetUpdates``,
…) и отдаёт его сессии. Подменив сессию, мы перехватываем этот поток целиком
— Dispatcher, роутеры, фильтры, FSM и типы остаются родными aiogram'овскими
и ничего не знают о MAX.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import httpx
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    GetMe,
    GetUpdates,
    SendChatAction,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import Update, User

from aiogram_max import converters
from aiogram_max.errors import MaxApiError, UnsupportedByMax

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

MAX_API_URL = "https://platform-api.max.ru"


class UnsupportedPolicy(StrEnum):
    """Что делать с методом Telegram, которого нет в MAX."""

    STRICT = "strict"
    """Поднять UnsupportedByMax. Дефолт: расхождение видно сразу."""

    LENIENT = "lenient"
    """Залоггировать и вернуть None. Бот продолжает работать урезанным."""


class MaxSession(BaseSession):
    """Сессия aiogram поверх MAX Bot API."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = MAX_API_URL,
        unsupported: UnsupportedPolicy = UnsupportedPolicy.STRICT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__()
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._unsupported = unsupported
        self._client = client or httpx.AsyncClient(timeout=90.0)
        # MAX помечает позицию в ленте событий непрозрачным marker'ом, aiogram
        # оперирует update_id/offset. Держим и то, и другое.
        self._marker: int | None = None
        self._update_id = 0
        # aiogram знает сообщение по целочисленному seq, MAX правит и удаляет
        # по строковому mid — храним соответствие.
        self._mid_by_seq: dict[int, str] = {}

    # --- BaseSession ---------------------------------------------------

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        handler = _HANDLERS.get(type(method))
        if handler is None:
            return self._reject(type(method).__name__)
        return await handler(self, bot, method)

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        """MAX отдаёт вложения прямой ссылкой, а не через file_id + getFile."""
        async with self._client.stream(
            "GET", url, headers=headers, timeout=timeout
        ) as response:
            if raise_for_status:
                response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size):
                yield chunk

    async def close(self) -> None:
        await self._client.aclose()

    # --- HTTP ----------------------------------------------------------

    async def _request(
        self,
        http_method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.request(
            http_method,
            f"{self._api_url}{path}",
            params=params,
            json=json,
            headers={"Authorization": self._token},
        )
        if response.status_code >= 400:
            raise MaxApiError(response.status_code, response.text)
        if not response.content:
            return {}
        data: dict[str, Any] = response.json()
        return data

    def _reject(self, method_name: str) -> None:
        if self._unsupported is UnsupportedPolicy.STRICT:
            raise UnsupportedByMax(method_name)
        logger.warning("%s не поддерживается MAX — вызов пропущен", method_name)
        return None

    # --- Трансляция методов --------------------------------------------

    async def _get_updates(self, bot: Bot, method: GetUpdates) -> list[Update]:
        params: dict[str, Any] = {}
        if method.timeout is not None:
            params["timeout"] = method.timeout
        if method.limit is not None:
            params["limit"] = method.limit
        if self._marker is not None:
            params["marker"] = self._marker

        data = await self._request("GET", "/updates", params=params)
        self._marker = data.get("marker", self._marker)

        updates: list[Update] = []
        for raw in data.get("updates", []):
            self._update_id += 1
            update = converters.to_update(raw, self._update_id)
            if update is None:
                logger.debug("MAX update пропущен: %s", raw.get("update_type"))
                continue
            self._remember_mid(raw)
            updates.append(update)
        return updates

    async def _send_message(self, bot: Bot, method: SendMessage) -> Any:
        payload: dict[str, Any] = {"text": method.text}
        attachment = converters.keyboard_to_attachment(
            method.reply_markup  # type: ignore[arg-type]
        )
        if attachment:
            payload["attachments"] = [attachment]

        data = await self._request(
            "POST",
            "/messages",
            params={"chat_id": method.chat_id},
            json=payload,
        )
        raw_message = data.get("message", {})
        self._remember_mid({"message": raw_message})
        return converters.to_message(raw_message)

    async def _edit_message_text(self, bot: Bot, method: EditMessageText) -> Any:
        mid = self._mid_by_seq.get(int(method.message_id or 0))
        if mid is None:
            raise MaxApiError(0, f"неизвестный message_id={method.message_id}")
        await self._request(
            "PUT", "/messages", params={"message_id": mid}, json={"text": method.text}
        )
        return True

    async def _delete_message(self, bot: Bot, method: DeleteMessage) -> bool:
        mid = self._mid_by_seq.get(int(method.message_id))
        if mid is None:
            raise MaxApiError(0, f"неизвестный message_id={method.message_id}")
        await self._request("DELETE", "/messages", params={"message_id": mid})
        return True

    async def _answer_callback(
        self, bot: Bot, method: AnswerCallbackQuery
    ) -> bool:
        body: dict[str, Any] = {}
        if method.text:
            body["notification"] = method.text
        await self._request(
            "POST",
            "/answers",
            params={"callback_id": method.callback_query_id},
            json=body,
        )
        return True

    async def _get_me(self, bot: Bot, method: GetMe) -> User:
        data = await self._request("GET", "/me")
        user = converters.to_user({**data, "is_bot": True})
        assert user is not None
        return user

    async def _send_chat_action(self, bot: Bot, method: SendChatAction) -> bool:
        """У MAX нет typing-индикатора — тихий no-op, а не ошибка.

        Это осознанное исключение из строгой политики: индикатор набора
        не влияет на смысл диалога, и ронять из-за него бота незачем.
        """
        logger.debug("SendChatAction проигнорирован: MAX не поддерживает typing")
        return True

    # --- Служебное ------------------------------------------------------

    def _remember_mid(self, raw: dict[str, Any]) -> None:
        message = raw.get("message") or {}
        body = message.get("body") or {}
        mid, seq = body.get("mid"), body.get("seq")
        if mid is not None and seq is not None:
            self._mid_by_seq[int(seq)] = str(mid)


_HANDLERS: dict[type[TelegramMethod[Any]], Any] = {
    GetUpdates: MaxSession._get_updates,
    SendMessage: MaxSession._send_message,
    EditMessageText: MaxSession._edit_message_text,
    DeleteMessage: MaxSession._delete_message,
    AnswerCallbackQuery: MaxSession._answer_callback,
    GetMe: MaxSession._get_me,
    SendChatAction: MaxSession._send_chat_action,
}
