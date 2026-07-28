"""Настройки бота: подписки на события и меню команд.

У MAX это не «один вебхук», как в Telegram, а список подписок, и команды
живут в профиле бота одним списком без областей видимости.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiogram.methods import (
    DeleteMyCommands,
    DeleteWebhook,
    GetMyCommands,
    GetWebhookInfo,
    SetMyCommands,
    SetWebhook,
)
from aiogram.types import BotCommand, WebhookInfo

from aiogram_max.methods.base import SessionCore

if TYPE_CHECKING:
    from aiogram import Bot


class SettingsMixin(SessionCore):
    """Вебхук и команды. Подмешивается в MaxSession."""

    async def _set_webhook(self, bot: Bot, method: SetWebhook) -> bool:
        """Подписка на события. У MAX это подписки, а не «один вебхук».

        Telegram держит ровно один адрес и молча заменяет прежний. MAX
        копит подписки списком, поэтому повторный вызов с тем же адресом
        создал бы дубль — сначала снимаем старую.
        """
        body: dict[str, Any] = {"url": method.url}
        if method.secret_token:
            body["secret"] = method.secret_token
        if method.allowed_updates:
            body["update_types"] = list(method.allowed_updates)
        await self._request("DELETE", "/subscriptions", params={"url": method.url})
        await self._request("POST", "/subscriptions", json=body)
        return True

    async def _delete_webhook(self, bot: Bot, method: DeleteWebhook) -> bool:
        """Снять все наши подписки: телеграмный deleteWebhook — без аргументов."""
        data = await self._request("GET", "/subscriptions")
        for sub in data.get("subscriptions", []):
            url = sub.get("url")
            if url:
                await self._request("DELETE", "/subscriptions", params={"url": url})
        return True

    async def _get_webhook_info(self, bot: Bot, method: GetWebhookInfo) -> WebhookInfo:
        """Первая подписка в терминах aiogram: у Telegram адрес всегда один."""
        data = await self._request("GET", "/subscriptions")
        subs = data.get("subscriptions", [])
        return WebhookInfo(
            url=subs[0].get("url", "") if subs else "",
            has_custom_certificate=False,
            pending_update_count=0,
        )

    async def _set_my_commands(self, bot: Bot, method: SetMyCommands) -> bool:
        if method.scope is not None or method.language_code is not None:
            self._degrade(
                "scope/language_code у команд",
                "MAX держит один список команд на бота",
            )
        commands = [
            {"name": c.command, "description": c.description} for c in method.commands
        ]
        await self._request("PATCH", "/me", json={"commands": commands})
        return True

    async def _get_my_commands(self, bot: Bot, method: GetMyCommands) -> list[BotCommand]:
        data = await self._request("GET", "/me")
        return [
            BotCommand(command=c["name"], description=c.get("description") or "")
            for c in (data.get("commands") or [])
        ]

    async def _delete_my_commands(self, bot: Bot, method: DeleteMyCommands) -> bool:
        await self._request("PATCH", "/me", json={"commands": []})
        return True
