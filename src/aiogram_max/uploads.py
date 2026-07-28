"""Загрузка вложений в MAX.

Схема у MAX двухшаговая и в справочнике описана наполовину, поэтому здесь
зафиксировано то, что выяснено на живом API:

1. ``POST /uploads?type=image|video|audio|file`` отдаёт ссылку, а для аудио
   и видео — ещё и токен сразу.
2. На полученный url кладётся сам файл, multipart. Дальше начинаются
   расхождения, потому что за типами стоят разные хранилища:

   * картинки и файлы — поле ``data``, ответ JSON. У картинок токен лежит
     внутри ``photos``, у файлов — на верхнем уровне.
   * аудио и видео — поле ``file`` (не ``data``!), в ответ приходит
     ``<retval>1</retval>``, никакого JSON. Токен берётся из шага 1.

   Перепутать поля нельзя: чужое имя даёт ``415 Unsupported Media Type``.

3. Токен уходит в сообщение как ``{"type": ..., "payload": {"token": ...}}``.

Для картинок MAX принимает и готовую ссылку вместо загрузки — тогда шаги
1 и 2 не нужны вовсе, достаточно ``payload.url``.
"""

from __future__ import annotations

import mimetypes
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


def upload_type_for(method_name: str, filename: str) -> str:
    """Тип загрузки с поправкой на формат файла.

    Телеграмная «анимация» — это либо gif, либо беззвучный mp4. У MAX это
    разные хранилища: gif принимает картиночное, видео-хранилище отвечает
    на него 415. Поэтому решаем по расширению, а не по имени метода.
    """
    base = UPLOAD_TYPES[method_name]
    if base == "video" and filename.lower().endswith(".gif"):
        return "image"
    return base

# Имя поля в multipart при заливке. Хранилища у типов разные, и каждое
# ждёт своё имя: чужое отвергается как 415 Unsupported Media Type.
UPLOAD_FIELDS: dict[str, str] = {
    "image": "data",
    "file": "data",
    "audio": "file",
    "video": "file",
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


# Чем подписывать файл, если по имени тип не угадывается. Хранилище MAX
# сверяет пару «расширение + content-type» и на несовпадение отвечает 415,
# поэтому octet-stream годится только для произвольных файлов.
FALLBACK_MIME: dict[str, str] = {
    "image": "image/jpeg",
    "video": "video/mp4",
    "audio": "audio/mpeg",
    "file": "application/octet-stream",
}


def guess_mime(filename: str, upload_type: str) -> str:
    """Content-type для заливки: по имени файла, иначе типовой по умолчанию."""
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or FALLBACK_MIME[upload_type]


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
