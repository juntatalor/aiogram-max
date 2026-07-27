"""Живой e2e: настоящий aiogram-бот против настоящего MAX.

Проверяет то, чего не проверить фейком: polling-цикл aiogram ходит в MAX,
хендлеры срабатывают, ответы и answer на callback доезжают до мессенджера.

    python scripts/live_e2e.py [секунд]

В коде ниже нет ничего специфичного для MAX, кроме одной строки create_bot.
"""

import asyncio
import logging
import os
import pathlib
import sys

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from aiogram_max import create_bot

ROOT = pathlib.Path(__file__).resolve().parent.parent
router = Router()


def load_token() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "MAX_BOT_TOKEN":
            return value.strip()
    return os.environ["MAX_BOT_TOKEN"]


@router.message(Command("start"))
async def on_start(message: Message) -> None:
    print(f"✔ хендлер /start сработал: chat={message.chat.id}", flush=True)
    await message.answer(
        "Живой e2e: /start дошёл до хендлера. Нажми кнопку.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="done:1")]
            ]
        ),
    )


@router.callback_query(F.data.startswith("done:"))
async def on_done(callback: CallbackQuery) -> None:
    print(f"✔ callback сработал: data={callback.data}", flush=True)
    await callback.answer("Принято!")
    if callback.message:
        await callback.message.answer("Круг замкнулся: MAX → aiogram → MAX.")


@router.message()
async def on_any(message: Message) -> None:
    print(f"✔ текст дошёл: {message.text!r}", flush=True)
    await message.answer(f"Эхо: {message.text}")


async def main(seconds: int) -> None:
    logging.basicConfig(level=logging.WARNING)
    bot = await create_bot(load_token())
    me = await bot.get_me()
    print(f"бот id={me.id} username={me.username}; слушаю {seconds} сек", flush=True)

    dp = Dispatcher()
    dp.include_router(router)

    task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    await asyncio.sleep(seconds)
    await dp.stop_polling()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await bot.session.close()
    print("остановлен", flush=True)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 180))
