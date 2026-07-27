"""Самый весомый тест: родной Dispatcher.start_polling крутится поверх MAX.

Отличается от test_session тем, что события не подсовываются через
feed_update — работает настоящий polling-цикл aiogram со своей внутренней
машинерией (startup-хуки, offset, обработка батчей).
"""

import asyncio

from aiogram import Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from tests.test_session import MESSAGE_CREATED, FakeMax, make_test_bot


async def test_native_polling_loop_delivers_max_events() -> None:
    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)
    delivered: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    router = Router()

    @router.message(Command("start"))
    async def on_start(message: Message) -> None:
        if not delivered.done():
            delivered.set_result(message.text or "")

    dp = Dispatcher()
    dp.include_router(router)

    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    try:
        text = await asyncio.wait_for(delivered, timeout=5)
    finally:
        await dp.stop_polling()
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)
        await bot.session.close()

    assert text == "/start"
