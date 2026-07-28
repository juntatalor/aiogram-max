"""Вебхук и команды бота.

У Telegram вебхук один и заменяется молча, у MAX это список подписок —
разница видна в поведении setWebhook. Команды у MAX живут в профиле бота,
без областей видимости и языков.
"""

from typing import Any

import httpx
import pytest
from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from aiogram_max import UnsupportedByMax, UnsupportedPolicy, make_bot


class FakeSettings:
    """Мини-MAX для /subscriptions и /me."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.subscriptions: list[dict[str, Any]] = []
        self.commands: list[dict[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, body))

        if path == "/subscriptions":
            if request.method == "GET":
                return httpx.Response(200, json={"subscriptions": self.subscriptions})
            if request.method == "POST":
                self.subscriptions.append({"url": (body or {}).get("url")})
                return httpx.Response(200, json={"success": True})
            if request.method == "DELETE":
                url = request.url.params.get("url")
                self.subscriptions = [s for s in self.subscriptions if s["url"] != url]
                return httpx.Response(200, json={"success": True})
        if path == "/me":
            if request.method == "PATCH":
                self.commands = (body or {}).get("commands", [])
                return httpx.Response(200, json={"user_id": 1, "is_bot": True})
            return httpx.Response(
                200,
                json={
                    "user_id": 1,
                    "first_name": "bot",
                    "is_bot": True,
                    "commands": self.commands,
                },
            )
        return httpx.Response(200, json={})


def make_settings_bot(fake: FakeSettings, **kwargs: Any) -> Bot:
    client = httpx.AsyncClient(transport=fake.transport())
    return make_bot("max-token", bot_id=1, client=client, **kwargs)


async def test_set_webhook_replaces_instead_of_duplicating() -> None:
    """Telegram держит один адрес, MAX копит список.

    Без снятия прежней подписки повторный setWebhook на тот же адрес
    оставил бы дубль, и бот получал бы каждое событие дважды.
    """
    fake = FakeSettings()
    bot = make_settings_bot(fake)

    await bot.set_webhook("https://example.com/hook")
    await bot.set_webhook("https://example.com/hook")

    assert fake.subscriptions == [{"url": "https://example.com/hook"}]
    await bot.session.close()


async def test_delete_webhook_removes_every_subscription() -> None:
    """Телеграмный deleteWebhook без аргументов — значит снимаем все."""
    fake = FakeSettings()
    fake.subscriptions = [{"url": "https://a.test/h"}, {"url": "https://b.test/h"}]
    bot = make_settings_bot(fake)

    await bot.delete_webhook()

    assert fake.subscriptions == []
    await bot.session.close()


async def test_webhook_info_reports_first_subscription() -> None:
    fake = FakeSettings()
    fake.subscriptions = [{"url": "https://a.test/h"}]
    bot = make_settings_bot(fake)

    info = await bot.get_webhook_info()

    assert info.url == "https://a.test/h"
    await bot.session.close()


async def test_commands_round_trip() -> None:
    fake = FakeSettings()
    bot = make_settings_bot(fake)

    await bot.set_my_commands(
        [BotCommand(command="start", description="Начать")]
    )
    assert [c.command for c in await bot.get_my_commands()] == ["start"]

    await bot.delete_my_commands()
    assert await bot.get_my_commands() == []
    await bot.session.close()


async def test_command_scope_is_not_supported() -> None:
    """Областей видимости у команд MAX не знает — список один на бота."""
    fake = FakeSettings()
    bot = make_settings_bot(fake, unsupported=UnsupportedPolicy.STRICT)

    with pytest.raises(UnsupportedByMax, match="команд"):
        await bot.set_my_commands(
            [BotCommand(command="start", description="Начать")],
            scope=BotCommandScopeAllPrivateChats(),
        )
    await bot.session.close()
