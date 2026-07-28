"""Сборка aiogram-бота, говорящего в MAX."""

from __future__ import annotations

import httpx
from aiogram import Bot

from aiogram_max.markup import MarkupPolicy
from aiogram_max.session import MAX_API_URL, MaxSession, UnsupportedPolicy

# aiogram проверяет форму токена: "<цифры>:<непустая строка>", а Bot.id парсит
# из левой части. Токен MAX такой формы не имеет, поэтому левую часть
# подставляем сами — туда идёт настоящий id бота. Сам токен уходит в
# MaxSession и дальше в заголовок Authorization.
_UNKNOWN_BOT_ID = 0


def make_bot(
    max_token: str,
    *,
    bot_id: int = _UNKNOWN_BOT_ID,
    api_url: str = MAX_API_URL,
    unsupported: UnsupportedPolicy = UnsupportedPolicy.WARN,
    markup: MarkupPolicy = MarkupPolicy.CONVERT,
    client: httpx.AsyncClient | None = None,
    **bot_kwargs: object,
) -> Bot:
    """Вернуть обычный ``aiogram.Bot``, ходящий в MAX вместо Telegram.

    Всё, что выше транспорта — Dispatcher, роутеры, фильтры, FSM, типы —
    остаётся стандартным aiogram.

    ``bot_id`` нужен, только если важен ``bot.id`` (мультиботовые сценарии,
    ключи в логах). Не знаете id — берите :func:`create_bot`, он спросит
    его у MAX сам.
    """
    session = MaxSession(
        max_token,
        api_url=api_url,
        unsupported=unsupported,
        markup=markup,
        client=client,
    )
    return Bot(token=f"{bot_id}:max", session=session, **bot_kwargs)  # type: ignore[arg-type]


async def create_bot(
    max_token: str,
    *,
    api_url: str = MAX_API_URL,
    unsupported: UnsupportedPolicy = UnsupportedPolicy.WARN,
    markup: MarkupPolicy = MarkupPolicy.CONVERT,
    client: httpx.AsyncClient | None = None,
    **bot_kwargs: object,
) -> Bot:
    """То же, что :func:`make_bot`, но с настоящим ``bot.id`` из ``GET /me``.

    Один лишний запрос на старте — зато ``bot.id`` не ноль, а username
    доезжает до фильтра ``Command``, который разбирает ``/start@bot``.
    """
    probe = make_bot(max_token, api_url=api_url, unsupported=unsupported, client=client)
    try:
        me = await probe.get_me()
    finally:
        await probe.session.close()

    return make_bot(
        max_token,
        bot_id=me.id,
        api_url=api_url,
        unsupported=unsupported,
        markup=markup,
        client=client,
        **bot_kwargs,
    )
