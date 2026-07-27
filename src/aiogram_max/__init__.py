"""aiogram-max: запускает aiogram-бота в мессенджере MAX.

Код бота не меняется — подменяется только транспорт::

    from aiogram import Dispatcher
    from aiogram_max import make_bot

    bot = make_bot(max_token="...")
    await Dispatcher().start_polling(bot)
"""

from aiogram_max.bot import create_bot, make_bot
from aiogram_max.errors import (
    AiogramMaxError,
    MaxApiError,
    NotImplementedYet,
    UnsupportedByMax,
)
from aiogram_max.session import (
    MAX_API_URL,
    NOT_IMPLEMENTED_PR_WELCOME,
    MaxSession,
    UnsupportedPolicy,
)

__all__ = [
    "MAX_API_URL",
    "NOT_IMPLEMENTED_PR_WELCOME",
    "AiogramMaxError",
    "MaxApiError",
    "MaxSession",
    "NotImplementedYet",
    "UnsupportedByMax",
    "UnsupportedPolicy",
    "create_bot",
    "make_bot",
]
