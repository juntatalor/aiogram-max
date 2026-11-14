"""События ниже разбора отдаётся потребителю: MaxSession.dropped_events."""


import httpx
import pytest

from aiogram_max import make_bot
from aiogram_max.session import MaxSession


def _handler(request: httpx.Request) -> httpx.Response:
    """Отдаёт пачку из трёх событий: кнопка и две «тихих» отписки."""
    body = {
            "updates": [
                {
                    "update_type": "message_callback",
                    "callback": {
                        "timestamp": 1,
                        "callback_id": "cb1",
                        "user": {"user_id": 42, "first_name": "Т"},
                        "payload": "menu:main",
                    },
                    "message": {
                        "recipient": {"chat_type": "dialog", "chat_id": 100},
                        "timestamp": 1,
                        "body": {"mid": "m1", "seq": 1, "text": "текст"},
                        "sender": {"user_id": 42, "first_name": "Т"},
                    },
                    "timestamp": 1,
                },
                {
                    "update_type": "bot_stopped",
                    "chat_id": 100,
                    "user": {"user_id": 42, "first_name": "Т"},
                    "timestamp": 2,
                },
                {
                    "update_type": "dialog_removed",
                    "chat_id": 200,
                    "user": {"user_id": 55},
                    "timestamp": 3,
                },
            ],
            "marker": 11,
        }
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_dropped_events_after_get_updates() -> None:
    """get_updates отдаёт конвертируемое, dropped_events — тихие события."""
    transport = httpx.MockTransport(_handler)
    bot = make_bot("t", client=httpx.AsyncClient(transport=transport, timeout=5))
    try:
        session = bot.session
        assert isinstance(session, MaxSession)
        updates = await bot.get_updates(offset=0, timeout=0, limit=10)
        # Кнопка + два пустых Update от тихих событий: позиция двигается
        # штатно, потребитель видит kind=unknown и не теряет поток.
        assert len(updates) == 3
        assert updates[0].callback_query is not None
        assert updates[1].message is None and updates[1].callback_query is None
        assert updates[2].message is None
        dropped = session.dropped_events
        assert [d["update_type"] for d in dropped] == ["bot_stopped", "dialog_removed"]
        assert dropped[0]["user_id"] == 42
        assert dropped[1]["user_id"] == 55
        # пачка заменяется следующей, не копится вечно
        assert len(session.dropped_events) == 2
    finally:
        await bot.session.close()
