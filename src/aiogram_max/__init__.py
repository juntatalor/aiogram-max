"""aiogram-max: запускает aiogram-бота в мессенджере MAX.

Код бота не меняется — подменяется только транспорт::

    from aiogram import Dispatcher
    from aiogram_max import make_bot

    bot = make_bot(max_token="...")
    await Dispatcher().start_polling(bot)
"""

from aiogram_max.bot import make_bot
from aiogram_max.errors import AiogramMaxError, MaxApiError, UnsupportedByMax
from aiogram_max.session import MAX_API_URL, MaxSession, UnsupportedPolicy

__all__ = [
    "MAX_API_URL",
    "AiogramMaxError",
    "MaxApiError",
    "MaxSession",
    "UnsupportedByMax",
    "UnsupportedPolicy",
    "make_bot",
]
