"""Вложения: отправка файлов, альбомы, замена медиа.

Здесь же собрана вся возня с загрузкой — она у MAX двухшаговая и разная для
разных типов файлов (подробности в ``uploads.py`` и в docs/method-coverage).
Отдельный модуль, потому что это самая объёмная и самая капризная часть
трансляции: три подводных камня из восьми задокументированных — отсюда.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx
from aiogram.methods import (
    EditMessageCaption,
    EditMessageMedia,
    EditMessageReplyMarkup,
    ForwardMessage,
    SendLocation,
    SendMediaGroup,
    SendSticker,
    SetChatPhoto,
    TelegramMethod,
)

from aiogram_max import converters
from aiogram_max.errors import MaxApiError
from aiogram_max.methods.base import SessionCore
from aiogram_max.uploads import (
    MEDIA_METHODS,
    STORAGES,
    guess_mime,
    read_input_file,
    token_from_upload,
    upload_type_for,
)

if TYPE_CHECKING:
    from aiogram import Bot


# Сразу после заливки MAX ещё обрабатывает файл и отвечает на отправку
# 400 attachment.not.ready. Ждём с нарастающей паузой.
ATTACHMENT_RETRY_ATTEMPTS = 8
ATTACHMENT_RETRY_DELAY = 0.5
ATTACHMENT_RETRY_MAX_DELAY = 4.0

logger = logging.getLogger(__name__)


class MediaMixin(SessionCore):
    """Методы про вложения. Подмешивается в MaxSession."""

    async def _send_media(self, bot: Bot, method: TelegramMethod[Any]) -> Any:
        """SendPhoto / SendDocument / SendVideo / SendAudio → вложение MAX.

        Общий путь для всех медиа-методов: тип вложения и имя поля с файлом
        берём из таблиц, дальше загрузка одинаковая.
        """
        name = type(method).__name__
        file = getattr(method, MEDIA_METHODS[name].file_attr)
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

        return await self._post_message(
            method.chat_id,  # type: ignore[attr-defined]
            payload,
            wait_attachment=True,
        )

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
        field = STORAGES[upload_type].field
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

    async def _edit_reply_markup(self, bot: Bot, method: EditMessageReplyMarkup) -> bool:
        """Заменить клавиатуру у отправленного сообщения.

        В MAX клавиатура — обычное вложение, поэтому правка идёт тем же
        PUT /messages. Пустой reply_markup убирает кнопки.
        """
        mid = self._require_mid(method.message_id)
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

    async def _send_location(self, bot: Bot, method: SendLocation) -> Any:
        """Точка на карте.

        Координаты у MAX лежат на верхнем уровне вложения, а не в payload,
        как у остальных типов: с payload приходит «latitude cannot be null».
        """
        payload: dict[str, Any] = {
            "attachments": [
                {
                    "type": "location",
                    "latitude": method.latitude,
                    "longitude": method.longitude,
                }
            ]
        }
        return await self._post_message(method.chat_id, payload)

    async def _send_media_group(self, bot: Bot, method: SendMediaGroup) -> Any:
        """Альбом. У MAX это просто несколько вложений в одном сообщении.

        Подпись Telegram берёт у первого элемента — здесь она становится
        текстом сообщения.
        """
        attachments: list[dict[str, Any]] = []
        caption: str | None = None
        parse_mode: Any = None
        for item in method.media:
            kind = {
                "photo": "image",
                "video": "video",
                "audio": "audio",
                "document": "file",
            }.get(item.type)
            if kind is None:
                self._degrade(f"media {item.type}", "нет аналога в MAX")
                continue
            attachments.append(await self._upload_attachment(bot, kind, item.media))
            if caption is None and getattr(item, "caption", None):
                caption = item.caption
                parse_mode = getattr(item, "parse_mode", None)

        payload: dict[str, Any] = {"attachments": attachments}
        if caption:
            text, fmt = self._render_markup(bot, caption, parse_mode, None)
            payload["text"] = text
            if fmt is not None:
                payload["format"] = fmt
        return await self._post_message(method.chat_id, payload, wait_attachment=True)

    async def _forward_message(self, bot: Bot, method: ForwardMessage) -> Any:
        """Пересылка: у MAX это ссылка типа forward на исходное сообщение."""
        mid = self._require_mid(method.message_id)
        return await self._post_message(
            method.chat_id, {"link": {"type": "forward", "mid": mid}}
        )

    async def _edit_caption(self, bot: Bot, method: EditMessageCaption) -> bool:
        """Подпись у MAX — это текст сообщения, правится тем же PUT."""
        mid = self._require_mid(method.message_id)
        text, fmt = self._render_markup(
            bot, method.caption, method.parse_mode, method.caption_entities
        )
        body: dict[str, Any] = {"text": text}
        if fmt is not None:
            body["format"] = fmt
        await self._request("PUT", "/messages", params={"message_id": mid}, json=body)
        return True

    async def _edit_media(self, bot: Bot, method: EditMessageMedia) -> bool:
        """Заменить вложение у отправленного сообщения.

        Новый файл заливается заново: ссылок на старое вложение MAX не даёт,
        да и смысла переиспользовать их нет. Подпись, если она задана,
        едет тем же запросом — у MAX это просто текст сообщения.
        """
        mid = self._require_mid(method.message_id)

        media = method.media
        kind = {
            "photo": "image",
            "video": "video",
            "audio": "audio",
            "document": "file",
        }.get(media.type)
        if kind is None:
            self._degrade(f"media {media.type}", "нет аналога в MAX")
            return True

        attachment = await self._upload_attachment(bot, kind, media.media)
        body: dict[str, Any] = {"attachments": [attachment]}
        caption = getattr(media, "caption", None)
        if caption:
            text, fmt = self._render_markup(
                bot, caption, getattr(media, "parse_mode", None), None
            )
            body["text"] = text
            if fmt is not None:
                body["format"] = fmt
        await self._request("PUT", "/messages", params={"message_id": mid}, json=body)
        return True

    async def _set_chat_photo(self, bot: Bot, method: SetChatPhoto) -> bool:
        """Иконка чата: заливаем картинку и подставляем токен в PATCH.

        Ссылку вместо токена MAX не принимает — отвечает internal.error.
        """
        attachment = await self._upload_attachment(bot, "image", method.photo)
        token = attachment.get("payload", {}).get("token")
        if not token:
            self._degrade(
                "SetChatPhoto по ссылке", "MAX принимает только загруженный файл"
            )
            return True
        await self._request(
            "PATCH", f"/chats/{method.chat_id}", json={"icon": {"token": token}}
        )
        return True

    async def _send_sticker(self, bot: Bot, method: SendSticker) -> Any:
        """Стикеры MAX опознаёт по своему коду, телеграмный file_id ему чужой.

        Библиотека не выдумывает соответствие: код стикера MAX бот должен
        знать сам и передать строкой.
        """
        code = method.sticker if isinstance(method.sticker, str) else None
        if not code:
            return self._reject("SendSticker")
        return await self._post_message(
            method.chat_id,
            {"attachments": [{"type": "sticker", "payload": {"code": code}}]},
        )
