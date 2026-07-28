"""MaxSession — транспорт aiogram, говорящий с MAX Bot API.

Весь исходящий трафик aiogram проходит через ``BaseSession.make_request``:
Bot собирает типизированный объект метода (``SendMessage``, ``GetUpdates``,
…) и отдаёт его сессии. Подменив сессию, мы перехватываем этот поток целиком
— Dispatcher, роутеры, фильтры, FSM и типы остаются родными aiogram'овскими
и ничего не знают о MAX.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import httpx
from aiogram.client.default import Default
from aiogram.client.session.base import BaseSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    EditMessageText,
    GetFile,
    GetMe,
    GetUpdates,
    SendAnimation,
    SendAudio,
    SendChatAction,
    SendDocument,
    SendMessage,
    SendPhoto,
    SendVideo,
    SendVoice,
    TelegramMethod,
)
from aiogram.types import File, MessageEntity, Update, User

from aiogram_max import converters
from aiogram_max.errors import MaxApiError, NotImplementedYet, UnsupportedByMax
from aiogram_max.markup import MarkupPolicy, entities_to_html, markdown_to_html
from aiogram_max.uploads import (
    FILE_FIELDS,
    UPLOAD_FIELDS,
    guess_mime,
    read_input_file,
    token_from_upload,
    upload_type_for,
)

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

MAX_API_URL = "https://platform-api.max.ru"

# Сразу после заливки MAX ещё обрабатывает файл и отвечает на отправку
# 400 attachment.not.ready. Ждём с нарастающей паузой.
ATTACHMENT_RETRY_ATTEMPTS = 8
ATTACHMENT_RETRY_DELAY = 0.5
ATTACHMENT_RETRY_MAX_DELAY = 4.0

# Методы, у которых в MAX аналог есть, а у нас руки не дошли. Отличаются от
# UnsupportedByMax тем, что чинятся патчем, а не свойствами платформы.
NOT_IMPLEMENTED_PR_WELCOME: dict[str, str] = {
    "SendMediaGroup": "MAX: несколько attachments в одном POST /messages",
    "SendLocation": "MAX: attachment type=location",
    "SendSticker": "MAX: attachment type=sticker",
    "EditMessageCaption": "MAX: PUT /messages",
    "EditMessageMedia": "MAX: PUT /messages с attachments",
    "ForwardMessage": "MAX: POST /messages с link type=forward",
    # Бот
    "SetMyCommands": "MAX: PATCH /me/commands",
    "GetMyCommands": "MAX: GET /me, поле commands",
    "DeleteMyCommands": "MAX: PATCH /me/commands с пустым списком",
    "SetWebhook": "MAX: POST /subscriptions",
    "DeleteWebhook": "MAX: DELETE /subscriptions",
    "GetWebhookInfo": "MAX: GET /subscriptions",
    # Чаты
    "GetChat": "MAX: GET /chats/{chat_id}",
    "LeaveChat": "MAX: DELETE /chats/{chat_id}/members/me",
    "SetChatTitle": "MAX: PATCH /chats/{chat_id}",
    "SetChatDescription": "MAX: PATCH /chats/{chat_id}",
    "SetChatPhoto": "MAX: PATCH /chats/{chat_id}",
    "PinChatMessage": "MAX: PUT /chats/{chat_id}/pin",
    "UnpinChatMessage": "MAX: DELETE /chats/{chat_id}/pin",
    "UnpinAllChatMessages": "MAX: DELETE /chats/{chat_id}/pin",
    # Участники
    "GetChatAdministrators": "MAX: GET /chats/{chat_id}/members/admins",
    "PromoteChatMember": "MAX: POST /chats/{chat_id}/members/admins",
    "GetChatMember": "MAX: GET /chats/{chat_id}/members",
    "GetChatMemberCount": "MAX: GET /chats/{chat_id}/members",
    "BanChatMember": "MAX: DELETE /chats/{chat_id}/members (у MAX это удаление, не бан)",
    "UnbanChatMember": "MAX: POST /chats/{chat_id}/members",
}


class UnsupportedPolicy(StrEnum):
    """Что делать с тем, чего в MAX нет.

    Расхождения бывают трёх видов, и политика покрывает первые два:

    1. Метода нет вовсе (``SendPoll``) — ``_reject``.
    2. Метод есть, а параметра нет (кнопка ``web_app``) — ``_degrade``.
    3. Семантика другая (``message_id`` против строкового ``mid``) — это
       не расхождение, а работа слоя конвертации, политики не касается.
    """

    WARN = "warn"
    """Предупреждение в лог, вызов продолжается урезанным. По умолчанию."""

    STRICT = "strict"
    """Поднять UnsupportedByMax. Включается явно при создании бота."""


class MaxSession(BaseSession):
    """Сессия aiogram поверх MAX Bot API."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = MAX_API_URL,
        unsupported: UnsupportedPolicy = UnsupportedPolicy.WARN,
        markup: MarkupPolicy = MarkupPolicy.CONVERT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # file="{path}" — ключевой момент для скачивания вложений. aiogram
        # строит ссылку на файл как api.file_url(token, file_path); у MAX
        # вложение уже приходит готовым URL, и шаблон отдаёт его как есть.
        super().__init__(
            api=TelegramAPIServer(base=f"{api_url}/{{method}}", file="{path}")
        )
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._unsupported = unsupported
        self._markup = markup
        self._client = client or httpx.AsyncClient(timeout=90.0)
        # Позиция в ленте событий: у MAX это marker, у aiogram — offset.
        # Смысл один и тот же, поэтому значения сшиты (см. _get_updates).
        self._marker: int | None = None
        self._last_update_id = 0
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
        name = type(method).__name__
        handler = _HANDLERS.get(type(method))
        if handler is not None:
            return await handler(self, bot, method)
        if name in NOT_IMPLEMENTED_PR_WELCOME:
            raise NotImplementedYet(name, NOT_IMPLEMENTED_PR_WELCOME[name])
        return self._reject(name)

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
        """Метода нет в MAX целиком."""
        if self._unsupported is UnsupportedPolicy.STRICT:
            raise UnsupportedByMax(method_name)
        logger.warning("%s не поддерживается MAX — вызов пропущен", method_name)
        return None

    def _degrade(self, what: str, detail: str) -> None:
        """Метод отработает, но часть запрошенного потерялась.

        Молча выбрасывать нельзя: «кнопка исчезла, а бот не упал» — ровно
        тот баг, который потом ищут часами.
        """
        if self._unsupported is UnsupportedPolicy.STRICT:
            raise UnsupportedByMax(f"{what} ({detail})")
        logger.warning("%s не поддерживается MAX и отброшен: %s", what, detail)

    # --- Разметка -------------------------------------------------------

    def _render_markup(
        self,
        bot: Bot,
        text: str | None,
        parse_mode: str | Default | None,
        entities: list[MessageEntity] | None,
    ) -> tuple[str, str | None]:
        """Текст aiogram → (текст для MAX, значение format).

        Телеграмная разметка приходит двумя способами — ``parse_mode`` над
        сырым текстом либо ``entities`` со смещениями. Оба переводим в html:
        это единственный формат MAX, покрывающий всю модель Telegram
        (в частности подчёркивание, которого в markdown у MAX нет).

        ``MarkupPolicy.RAW`` возвращает текст нетронутым — для тех, кто
        предпочитает форматировать под MAX самостоятельно.
        """
        body = text or ""
        # parse_mode может прийти сентинелом Default — реальное значение
        # тогда лежит в настройках бота, а не в самом методе.
        if isinstance(parse_mode, Default):
            parse_mode = bot.default.parse_mode
        mode = str(parse_mode) if parse_mode else None

        if self._markup is MarkupPolicy.RAW:
            return body, converters.parse_mode_to_format(mode)

        if entities:
            return entities_to_html(body, entities, self._degrade), "html"
        if mode is None:
            return body, None
        if mode == "HTML":
            return body, "html"
        return markdown_to_html(body, self._degrade), "html"

    # --- Трансляция методов --------------------------------------------

    async def _get_updates(self, bot: Bot, method: GetUpdates) -> list[Update]:
        """Лента событий MAX в терминах aiogram.

        ``marker`` у MAX — позиция «следующего ожидаемого события», ровно тот
        же смысл, что у телеграмного ``offset``. Поэтому оба конца
        сшиваются напрямую: пришедший ``offset`` уходит как ``marker``, а
        ``update_id`` события — его позиция в ленте, то есть ``marker - 1``
        для последнего события пачки.

        Раньше ``update_id`` был счётчиком в памяти сессии, и это ломало
        любого потребителя с дедупом: после рестарта нумерация начиналась
        заново, и свежие события выглядели уже обработанными.
        """
        if method.offset:
            self._marker = method.offset

        params: dict[str, Any] = {}
        if method.timeout is not None:
            params["timeout"] = method.timeout
        if method.limit is not None:
            params["limit"] = method.limit
        if self._marker is not None:
            params["marker"] = self._marker

        data = await self._request("GET", "/updates", params=params)
        raws = data.get("updates", [])
        marker = data.get("marker", self._marker)
        self._marker = marker

        base = self._first_id_of_batch(marker, len(raws))
        updates: list[Update] = []
        for position, raw in enumerate(raws):
            update_id = base + position
            self._last_update_id = update_id
            update = converters.to_update(raw, update_id)
            if update is None:
                logger.debug("MAX update пропущен: %s", raw.get("update_type"))
                continue
            self._remember_mid(raw)
            updates.append(update)
        return updates

    def _first_id_of_batch(self, marker: int | None, count: int) -> int:
        """Позиция первого события пачки.

        Обычно это ``marker - count``. Если MAX не прислал marker или тот
        сдвинулся меньше, чем на размер пачки, — продолжаем от последнего
        выданного id. Наложение id между пачками страшнее, чем расхождение
        с marker'ом: потребитель с дедупом молча потеряет событие.
        """
        if marker is None:
            return self._last_update_id + 1
        return max(marker - count, self._last_update_id + 1)

    async def _send_message(self, bot: Bot, method: SendMessage) -> Any:
        text, fmt = self._render_markup(
            bot, method.text, method.parse_mode, method.entities
        )
        payload: dict[str, Any] = {"text": text}
        if fmt is not None:
            payload["format"] = fmt

        attachment = converters.keyboard_to_attachment(
            method.reply_markup,  # type: ignore[arg-type]
            degrade=self._degrade,
        )
        if attachment:
            payload["attachments"] = [attachment]

        # Параметры, у которых в MAX есть прямой аналог.
        if method.disable_notification is not None:
            payload["notify"] = not method.disable_notification
        if method.reply_to_message_id is not None:
            mid = self._mid_by_seq.get(int(method.reply_to_message_id))
            if mid is None:
                self._degrade(
                    "reply_to_message_id",
                    f"неизвестный message_id={method.reply_to_message_id}",
                )
            else:
                payload["link"] = {"type": "reply", "mid": mid}

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
        text, fmt = self._render_markup(
            bot, method.text, method.parse_mode, method.entities
        )
        body: dict[str, Any] = {"text": text}
        if fmt is not None:
            body["format"] = fmt
        await self._request("PUT", "/messages", params={"message_id": mid}, json=body)
        return True

    async def _delete_message(self, bot: Bot, method: DeleteMessage) -> bool:
        mid = self._mid_by_seq.get(int(method.message_id))
        if mid is None:
            raise MaxApiError(0, f"неизвестный message_id={method.message_id}")
        await self._request("DELETE", "/messages", params={"message_id": mid})
        return True

    async def _answer_callback(self, bot: Bot, method: AnswerCallbackQuery) -> bool:
        """Ответ на callback. Без текста — тихий no-op, запрос не уходит.

        В Telegram пустой ``answer()`` — обычное дело: он просто снимает
        индикатор загрузки на кнопке, и боты зовут его почти всегда. У MAX
        такой семантики нет: POST /answers с пустым телом отвечает
        ``400 proto.payload`` — «`message` or `notification` required».

        Отправлять ради этого пустое уведомление нельзя (юзер увидит пустой
        popup), а ронять вызов — тем более: пустой ``answer()`` стоит в
        каждом callback-хендлере, и исключение из него убивает обработку
        самого клика. Поэтому отправляем, только когда есть что отправить.
        """
        if not method.text:
            logger.debug(
                "answer_callback без текста пропущен: у MAX нет пустого ответа"
            )
            return True
        await self._request(
            "POST",
            "/answers",
            params={"callback_id": method.callback_query_id},
            json={"notification": method.text},
        )
        return True

    async def _send_media(self, bot: Bot, method: TelegramMethod[Any]) -> Any:
        """SendPhoto / SendDocument / SendVideo / SendAudio → вложение MAX.

        Общий путь для всех медиа-методов: тип вложения и имя поля с файлом
        берём из таблиц, дальше загрузка одинаковая.
        """
        name = type(method).__name__
        file = getattr(method, FILE_FIELDS[name])
        upload_type = upload_type_for(name, getattr(file, "filename", "") or "")

        payload_attachment = await self._upload_attachment(bot, upload_type, file)
        caption, fmt = self._render_markup(
            bot,
            getattr(method, "caption", None),
            getattr(method, "parse_mode", None),
            getattr(method, "caption_entities", None),
        )

        payload: dict[str, Any] = {"attachments": [payload_attachment]}
        if caption:
            payload["text"] = caption
            if fmt is not None:
                payload["format"] = fmt

        keyboard = converters.keyboard_to_attachment(
            getattr(method, "reply_markup", None),
            degrade=self._degrade,
        )
        if keyboard:
            payload["attachments"].append(keyboard)
        if getattr(method, "disable_notification", None) is not None:
            payload["notify"] = not method.disable_notification  # type: ignore[attr-defined]

        data = await self._send_when_attachment_ready(
            method.chat_id,  # type: ignore[attr-defined]
            payload,
        )
        raw_message = data.get("message", {})
        self._remember_mid({"message": raw_message})
        return converters.to_message(raw_message)

    async def _send_when_attachment_ready(
        self, chat_id: Any, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Отправить сообщение с вложением, дождавшись обработки файла.

        Своего рода рукопожатие, которого нет в Telegram: сразу после
        заливки MAX отвечает на отправку ``400 attachment.not.ready`` —
        файл ещё обрабатывается на его стороне. Ждём и повторяем; ошибку
        отдаём наружу, только если файл так и не доехал.
        """
        delay = ATTACHMENT_RETRY_DELAY
        for attempt in range(1, ATTACHMENT_RETRY_ATTEMPTS + 1):
            try:
                return await self._request(
                    "POST", "/messages", params={"chat_id": chat_id}, json=payload
                )
            except MaxApiError as exc:
                if "attachment.not.ready" not in str(exc):
                    raise
                if attempt == ATTACHMENT_RETRY_ATTEMPTS:
                    raise
                logger.debug(
                    "MAX ещё обрабатывает вложение, попытка %s из %s",
                    attempt,
                    ATTACHMENT_RETRY_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, ATTACHMENT_RETRY_MAX_DELAY)
        raise MaxApiError(0, "недостижимо: цикл ожидания вложения")

    async def _upload_attachment(
        self, bot: Bot, upload_type: str, file: Any
    ) -> dict[str, Any]:
        """Залить файл и вернуть готовое вложение для POST /messages.

        Строка вместо файла — это telegram-овский file_id либо ссылка.
        Для картинок MAX принимает прямую ссылку, в остальных случаях
        переносить нечего: file_id чужой платформы там ничего не значит.
        """
        if isinstance(file, str):
            if file.startswith(("http://", "https://")) and upload_type == "image":
                return {"type": "image", "payload": {"url": file}}
            self._degrade(
                f"{upload_type} по строке",
                "MAX не знает telegram file_id; передавайте файл или ссылку на картинку",
            )
            return {"type": upload_type, "payload": {"url": file}}

        slot = await self._request("POST", "/uploads", params={"type": upload_type})
        url = slot.get("url")
        if not url:
            raise MaxApiError(0, f"MAX не выдал ссылку для загрузки: {slot}")

        content = await read_input_file(file, bot)
        filename = getattr(file, "filename", None) or "file"
        field = UPLOAD_FIELDS[upload_type]
        mime = guess_mime(filename, upload_type)
        response = await self._client.post(
            url,
            files={field: (filename, content, mime)},
            timeout=httpx.Timeout(300.0),
        )
        if response.status_code >= 400:
            raise MaxApiError(response.status_code, response.text)
        # Аудио и видео отдают токен ещё на шаге со слотом, а в ответ на
        # саму заливку присылают <retval>1</retval> — разбирать нечего.
        token = slot.get("token") or token_from_upload(response.json())
        return {"type": upload_type, "payload": {"token": token}}

    async def _edit_reply_markup(
        self, bot: Bot, method: EditMessageReplyMarkup
    ) -> bool:
        """Заменить клавиатуру у отправленного сообщения.

        В MAX клавиатура — обычное вложение, поэтому правка идёт тем же
        PUT /messages. Пустой reply_markup убирает кнопки.
        """
        mid = self._mid_by_seq.get(int(method.message_id or 0))
        if mid is None:
            raise MaxApiError(0, f"неизвестный message_id={method.message_id}")
        keyboard = converters.keyboard_to_attachment(
            method.reply_markup,
            degrade=self._degrade,
        )
        await self._request(
            "PUT",
            "/messages",
            params={"message_id": mid},
            json={"attachments": [keyboard] if keyboard else []},
        )
        return True

    async def _get_file(self, bot: Bot, method: GetFile) -> File:
        """У MAX нет getFile: ссылка на вложение приходит вместе с сообщением.

        Конвертер кладёт её в ``file_id``, здесь просто возвращаем её же как
        ``file_path`` — дальше aiogram скачает через ``stream_content``.
        """
        return File(
            file_id=method.file_id,
            file_unique_id=method.file_id,
            file_path=method.file_id,
        )

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
    GetFile: MaxSession._get_file,
    GetMe: MaxSession._get_me,
    SendChatAction: MaxSession._send_chat_action,
    EditMessageReplyMarkup: MaxSession._edit_reply_markup,
    SendPhoto: MaxSession._send_media,
    SendVideo: MaxSession._send_media,
    SendAudio: MaxSession._send_media,
    SendVoice: MaxSession._send_media,
    SendDocument: MaxSession._send_media,
    SendAnimation: MaxSession._send_media,
}
