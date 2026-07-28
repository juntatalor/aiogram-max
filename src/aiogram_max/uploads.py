"""Загрузка вложений в MAX.

Схема у MAX двухшаговая и в справочнике описана наполовину, поэтому здесь
зафиксировано то, что выяснено на живом API:

1. ``POST /uploads?type=image|video|audio|file`` отдаёт ``{"url": ...}``.
   Токена на этом шаге нет, вопреки тому, что написано в документации.
2. На полученный url кладётся сам файл, multipart, имя поля — ``data``.
   Ответ отличается по типам:

   * картинки — ``{"photos": {"<ключ>": {"token": "..."}}}``;
   * файлы, видео, аудио — ``{"fileId": ..., "token": "..."}``.

3. Токен уходит в сообщение как ``{"type": ..., "payload": {"token": ...}}``.

Для картинок MAX принимает и готовую ссылку вместо загрузки — тогда шаги
1 и 2 не нужны вовсе, достаточно ``payload.url``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiogram_max.errors import MaxApiError

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import InputFile

# aiogram-метод → тип загрузки MAX. Совпадает с типом вложения в сообщении.
UPLOAD_TYPES: dict[str, str] = {
    "SendPhoto": "image",
    "SendVideo": "video",
    "SendAudio": "audio",
    "SendVoice": "audio",
    "SendDocument": "file",
    "SendAnimation": "video",
}

# Поле метода, в котором лежит сам файл.
FILE_FIELDS: dict[str, str] = {
    "SendPhoto": "photo",
    "SendVideo": "video",
    "SendAudio": "audio",
    "SendVoice": "voice",
    "SendDocument": "document",
    "SendAnimation": "animation",
}


async def read_input_file(file: InputFile, bot: Bot) -> bytes:
    """Собрать файл целиком: aiogram отдаёт его асинхронными кусками."""
    chunks = [chunk async for chunk in file.read(bot)]
    return b"".join(chunks)


def token_from_upload(response: dict[str, Any]) -> str:
    """Достать токен из ответа заливки — форма зависит от типа файла."""
    if "token" in response:
        token: str = response["token"]
        return token
    photos = response.get("photos") or {}
    for item in photos.values():
        if isinstance(item, dict) and item.get("token"):
            photo_token: str = item["token"]
            return photo_token
    # Заливка отвечает 200 даже на отказ, ошибка лежит в теле — без этой
    # ветки наверх ушло бы невнятное «нет токена».
    if response.get("error_code") or response.get("error_data"):
        raise MaxApiError(
            int(response.get("error_code") or 0),
            f"MAX отверг файл: {response.get('error_data') or response}",
        )
    raise MaxApiError(0, f"в ответе загрузки нет токена: {response}")
