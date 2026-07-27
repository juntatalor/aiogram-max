"""Сборка aiogram-бота, говорящего в MAX."""

from __future__ import annotations

import httpx
from aiogram import Bot

from aiogram_max.session import MAX_API_URL, MaxSession, UnsupportedPolicy

# aiogram проверяет форму токена: "<цифры>:<непустая строка>". Токен MAX этой
# формы не имеет, а Bot.id парсится из левой части — подставляем заглушку.
# Настоящий токен уходит в MaxSession и дальше в заголовок Authorization.
_PLACEHOLDER_TOKEN = "0:max"


def make_bot(
    max_token: str,
    *,
    api_url: str = MAX_API_URL,
    unsupported: UnsupportedPolicy = UnsupportedPolicy.STRICT,
    client: httpx.AsyncClient | None = None,
    **bot_kwargs: object,
) -> Bot:
    """Вернуть обычный ``aiogram.Bot``, ходящий в MAX вместо Telegram.

    Всё, что выше транспорта — Dispatcher, роутеры, фильтры, FSM, типы —
    остаётся стандартным aiogram.
    """
    session = MaxSession(
        max_token,
        api_url=api_url,
        unsupported=unsupported,
        client=client,
    )
    return Bot(token=_PLACEHOLDER_TOKEN, session=session, **bot_kwargs)  # type: ignore[arg-type]
