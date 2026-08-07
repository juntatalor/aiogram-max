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
from aiogram.client.default import Default
from aiogram.client.session.base import BaseSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.methods import (
    AnswerCallbackQuery,
    BanChatMember,
    DeleteMessage,
    DeleteMyCommands,
    DeleteWebhook,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditMessageText,
    ForwardMessage,
    GetChat,
    GetChatAdministrators,
    GetChatMember,
    GetChatMemberCount,
    GetFile,
    GetMe,
    GetMyCommands,
    GetUpdates,
    GetWebhookInfo,
    LeaveChat,
    PinChatMessage,
    PromoteChatMember,
    SendAnimation,
    SendAudio,
    SendChatAction,
    SendDocument,
    SendLocation,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SendSticker,
    SendVideo,
    SendVoice,
    SetChatDescription,
    SetChatPhoto,
    SetChatTitle,
    SetMyCommands,
    SetWebhook,
    TelegramMethod,
    UnbanChatMember,
    UnpinAllChatMessages,
    UnpinChatMessage,
)
from aiogram.types import (
    File,
    MessageEntity,
    Update,
    User,
)

from aiogram_max import converters
from aiogram_max.errors import MaxApiError, NotImplementedYet, UnsupportedByMax
from aiogram_max.markup import MarkupPolicy, entities_to_html, markdown_to_html
from aiogram_max.methods import ChatsMixin, MediaMixin, SettingsMixin
from aiogram_max.mids import MidRegistry

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

MAX_API_URL = "https://platform-api.max.ru"

# Телеграмное действие → действие MAX. Список проверен на живом API:
# всё, чего здесь нет, MAX отвергает как proto.payload.
CHAT_ACTIONS: dict[str, str] = {
    "typing": "typing_on",
    "upload_photo": "sending_photo",
    "upload_video": "sending_video",
    "record_video": "sending_video",
    "upload_voice": "sending_audio",
    "record_voice": "sending_audio",
    "upload_document": "sending_file",
}

# Методы, у которых в MAX аналог есть, а у нас руки не дошли. Отличаются от
# UnsupportedByMax тем, что чинятся патчем, а не свойствами платформы.
NOT_IMPLEMENTED_PR_WELCOME: dict[str, str] = {
    # Бот
    # Чаты
    # Участники
}


class UnsupportedPolicy(StrEnum):
    """Что делать с тем, чего в MAX нет.

    Расхождения бывают трёх видов, и политика покрывает первые два:

    1. Метода нет вовсе (``SendPoll``) — ``_reject``.
    2. Метод есть, а параметра нет (кнопка ``web_app``) — ``_degrade``.
    3. Семантика другая (``message_id`` против строкового ``mid``) — это
       не расхождение, а работа слоя конвертации, политики не касается.
    """

    # Предупреждение в лог, вызов продолжается урезанным. По умолчанию.
    WARN = "warn"

    # Поднять UnsupportedByMax. Включается явно при создании бота.
    STRICT = "strict"


class MaxSession(MediaMixin, ChatsMixin, SettingsMixin, BaseSession):
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
        # aiogram знает сообщение по seq, MAX правит и удаляет по mid.
        # Реестр ограничен по размеру, см. mids.py.
        self._mids = MidRegistry()

    @property
    def marker(self) -> int | None:
        """Позиция в ленте событий MAX после последнего ``get_updates``.

        Нужна потребителю, который хранит позицию сам. Библиотека отдаёт
        наверх только те события, которые умеет разбирать, а незнакомые
        (``message_removed`` и прочие) тихо пропускает. Для потребителя это
        выглядит как пустой ответ: обрабатывать нечего, двигать позицию
        нечем — и следующий запрос вернёт то же самое событие. Опрос встаёт
        намертво, а вместе с ним и всё, что придёт после.

        Маркер показывает, до какой позиции лента уже прочитана, включая
        пропущенное. По нему потребитель перешагивает мёртвое событие.

        ``None`` — ни одного запроса ещё не было.
        """
        return self._marker

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
            mid = self._mids.get(int(method.reply_to_message_id))
            if mid is None:
                self._degrade(
                    "reply_to_message_id",
                    f"неизвестный message_id={method.reply_to_message_id}",
                )
            else:
                payload["link"] = {"type": "reply", "mid": mid}

        return await self._post_message(method.chat_id, payload)

    async def _edit_message_text(self, bot: Bot, method: EditMessageText) -> Any:
        mid = self._require_mid(method.message_id)
        text, fmt = self._render_markup(
            bot, method.text, method.parse_mode, method.entities
        )
        body: dict[str, Any] = {"text": text}
        if fmt is not None:
            body["format"] = fmt
        await self._request("PUT", "/messages", params={"message_id": mid}, json=body)
        return True

    async def _delete_message(self, bot: Bot, method: DeleteMessage) -> bool:
        mid = self._require_mid(method.message_id)
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
            logger.debug("answer_callback без текста пропущен: у MAX нет пустого ответа")
            return True
        await self._request(
            "POST",
            "/answers",
            params={"callback_id": method.callback_query_id},
            json={"notification": method.text},
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
        """Индикатор действия: «печатает», «отправляет фото» и так далее.

        Долгое время в библиотеке это был no-op с комментарием «у MAX нет
        typing-индикатора» — унаследованное заблуждение. Проверка на живом
        API показала обратное: POST /chats/{id}/actions работает и в группе,
        и в личке. Названия действий свои, телеграмное «typing» MAX не
        понимает вовсе, поэтому переводим.
        """
        action = CHAT_ACTIONS.get(str(method.action))
        if action is None:
            self._degrade(f"chat action {method.action}", "у MAX нет такого индикатора")
            return True
        await self._request(
            "POST",
            f"/chats/{method.chat_id}/actions",
            json={"action": action},
        )
        return True

    # --- Служебное ------------------------------------------------------

    def _require_mid(self, message_id: int | None) -> str:
        """Найти mid по message_id или объяснить, почему не вышло.

        Соответствие держится в памяти процесса, поэтому промах здесь
        обычно означает не опечатку, а сообщение, отправленное до рестарта.
        """
        mid = self._mids.get(int(message_id or 0))
        if mid is None:
            raise MaxApiError(
                0,
                f"неизвестный message_id={message_id}: соответствие mid живёт "
                "в памяти процесса и не переживает рестарт",
            )
        return mid

    async def _post_message(
        self, chat_id: Any, payload: dict[str, Any], *, wait_attachment: bool = False
    ) -> Any:
        """Отправить сообщение и превратить ответ в aiogram-Message.

        Общий хвост для всех отправок: текст, медиа, альбом, локация,
        пересылка, стикер. Отдельно вынесен, потому что каждая из них ещё и
        обязана запомнить mid — забудешь, и правка сообщения перестанет
        работать без единой ошибки.
        """
        if wait_attachment:
            data = await self._send_when_attachment_ready(chat_id, payload)
        else:
            data = await self._request(
                "POST", "/messages", params={"chat_id": chat_id}, json=payload
            )
        raw_message = data.get("message", {})
        self._remember_mid({"message": raw_message})
        return converters.to_message(raw_message)

    def _remember_mid(self, raw: dict[str, Any]) -> None:
        message = raw.get("message") or {}
        body = message.get("body") or {}
        mid, seq = body.get("mid"), body.get("seq")
        if mid is not None and seq is not None:
            self._mids.remember(int(seq), str(mid))


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
    GetChat: MaxSession._get_chat,
    GetChatMemberCount: MaxSession._get_chat_member_count,
    GetChatMember: MaxSession._get_chat_member,
    GetChatAdministrators: MaxSession._get_chat_administrators,
    LeaveChat: MaxSession._leave_chat,
    PinChatMessage: MaxSession._pin_message,
    UnpinChatMessage: MaxSession._unpin_message,
    UnpinAllChatMessages: MaxSession._unpin_message,
    SetChatTitle: MaxSession._set_chat_title,
    SetChatDescription: MaxSession._set_chat_description,
    PromoteChatMember: MaxSession._promote_member,
    BanChatMember: MaxSession._ban_member,
    UnbanChatMember: MaxSession._unban_member,
    SetWebhook: MaxSession._set_webhook,
    DeleteWebhook: MaxSession._delete_webhook,
    GetWebhookInfo: MaxSession._get_webhook_info,
    SetMyCommands: MaxSession._set_my_commands,
    GetMyCommands: MaxSession._get_my_commands,
    DeleteMyCommands: MaxSession._delete_my_commands,
    SendLocation: MaxSession._send_location,
    SendMediaGroup: MaxSession._send_media_group,
    ForwardMessage: MaxSession._forward_message,
    EditMessageCaption: MaxSession._edit_caption,
    SendSticker: MaxSession._send_sticker,
    EditMessageMedia: MaxSession._edit_media,
    SetChatPhoto: MaxSession._set_chat_photo,
}
