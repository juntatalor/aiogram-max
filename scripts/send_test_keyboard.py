"""Отправляет в MAX сообщение с inline-клавиатурой — через нашу же библиотеку.

Это не curl: сообщение уходит настоящим aiogram-вызовом bot.send_message
поверх MaxSession, то есть заодно проверяет весь путь отправки.

    python scripts/send_test_keyboard.py <chat_id>
"""

import asyncio
import os
import pathlib
import sys

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from aiogram_max import create_bot

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_token() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "MAX_BOT_TOKEN":
            return value.strip()
    return os.environ["MAX_BOT_TOKEN"]


async def main(chat_id: int) -> None:
    bot = await create_bot(load_token())
    try:
        me = await bot.get_me()
        print(f"бот: id={me.id} username={me.username}")

        message = await bot.send_message(
            chat_id=chat_id,
            text="Тест aiogram-max. Нажми любую кнопку — ловлю message_callback.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Принять", callback_data="accept:1"),
                        InlineKeyboardButton(text="✏️ Доработать", callback_data="edit:1"),
                    ],
                    [InlineKeyboardButton(text="Ссылка", url="https://max.ru")],
                ]
            ),
        )
        print(f"отправлено: message_id={message.message_id}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
