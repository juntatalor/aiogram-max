"""Демо: один и тот же код бота работает в Telegram и в MAX.

Запуск:
    MAX_BOT_TOKEN=... python examples/demo_bot.py max
    TELEGRAM_BOT_TOKEN=... python examples/demo_bot.py telegram

Ниже нет ни одного импорта из aiogram_max, кроме строки создания бота.
Хендлеры, фильтры, типы и FSM — обычный aiogram.
"""

import asyncio
import os
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

router = Router()


@router.message(Command("start"))
async def on_start(message: Message) -> None:
    await message.answer(
        "Привет! Это один и тот же код в двух мессенджерах.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Нажми меня", callback_data="ping")]
            ]
        ),
    )


@router.callback_query(F.data == "ping")
async def on_ping(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка работает")
    if callback.message:
        await callback.message.answer("pong")


async def main() -> None:
    platform = sys.argv[1] if len(sys.argv) > 1 else "max"

    if platform == "max":
        from aiogram_max import make_bot

        bot = make_bot(os.environ["MAX_BOT_TOKEN"])
    else:
        bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])

    dp = Dispatcher()
    dp.include_router(router)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
