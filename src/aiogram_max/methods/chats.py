"""Группы: чат, участники, админы, закреплённое, настройки чата.

Расхождений с Telegram здесь больше всего: у MAX нет бана, нет отдельных
прав администратора и нет выборки участника по id — см. комментарии на
местах и таблицу в docs/method-coverage.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiogram.methods import (
    BanChatMember,
    GetChat,
    GetChatAdministrators,
    GetChatMember,
    GetChatMemberCount,
    LeaveChat,
    PinChatMessage,
    PromoteChatMember,
    SetChatDescription,
    SetChatTitle,
    UnbanChatMember,
    UnpinAllChatMessages,
    UnpinChatMessage,
)
from aiogram.types import (
    ChatFullInfo,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberOwner,
)

from aiogram_max import converters
from aiogram_max.errors import MaxApiError
from aiogram_max.methods.base import SessionCore

if TYPE_CHECKING:
    from aiogram import Bot


class ChatsMixin(SessionCore):
    """Групповые методы. Подмешивается в MaxSession."""

    async def _get_chat(self, bot: Bot, method: GetChat) -> ChatFullInfo:
        data = await self._request("GET", f"/chats/{method.chat_id}")
        return converters.to_chat_full_info(data)

    async def _get_chat_member_count(self, bot: Bot, method: GetChatMemberCount) -> int:
        data = await self._request("GET", f"/chats/{method.chat_id}")
        return int(data.get("participants_count", 0))

    async def _get_chat_member(
        self, bot: Bot, method: GetChatMember
    ) -> ChatMemberOwner | ChatMemberAdministrator | ChatMemberMember:
        """Участник по id.

        Про себя MAX отвечает отдельной ручкой, про остальных приходится
        листать список: выборки по user_id у него нет.
        """
        if bot.id and int(method.user_id) == int(bot.id):
            data = await self._request("GET", f"/chats/{method.chat_id}/members/me")
            return converters.to_chat_member(data)
        data = await self._request("GET", f"/chats/{method.chat_id}/members")
        for raw in data.get("members", []):
            if int(raw.get("user_id", 0)) == int(method.user_id):
                return converters.to_chat_member(raw)
        raise MaxApiError(404, f"участник {method.user_id} не найден в чате")

    async def _get_chat_administrators(
        self, bot: Bot, method: GetChatAdministrators
    ) -> list[ChatMemberOwner | ChatMemberAdministrator | ChatMemberMember]:
        """Список админов. MAX отдаёт его только самим администраторам."""
        data = await self._request("GET", f"/chats/{method.chat_id}/members/admins")
        return [converters.to_chat_member(raw) for raw in data.get("members", [])]

    async def _leave_chat(self, bot: Bot, method: LeaveChat) -> bool:
        await self._request("DELETE", f"/chats/{method.chat_id}/members/me")
        return True

    async def _pin_message(self, bot: Bot, method: PinChatMessage) -> bool:
        mid = self._require_mid(method.message_id)
        body: dict[str, Any] = {"message_id": mid}
        if method.disable_notification is not None:
            body["notify"] = not method.disable_notification
        await self._request("PUT", f"/chats/{method.chat_id}/pin", json=body)
        return True

    async def _unpin_message(
        self, bot: Bot, method: UnpinChatMessage | UnpinAllChatMessages
    ) -> bool:
        """Открепление. У MAX закреплено одно сообщение, поэтому «снять
        закреплённое» и «снять все» — один и тот же вызов."""
        await self._request("DELETE", f"/chats/{method.chat_id}/pin")
        return True

    async def _set_chat_title(self, bot: Bot, method: SetChatTitle) -> bool:
        await self._request(
            "PATCH", f"/chats/{method.chat_id}", json={"title": method.title}
        )
        return True

    async def _set_chat_description(self, bot: Bot, method: SetChatDescription) -> None:
        """Описание чата боту недоступно.

        Проверено на живом API: PATCH /chats с description отвечает 200 и
        отдаёт обновлённый чат, но поле не меняется, а если послать одно
        описание без названия — приходит ошибка в теле при статусе 200.
        То есть вызов молча ничего не делает, и притворяться успехом нельзя.
        """
        return self._reject("SetChatDescription")

    async def _promote_member(self, bot: Bot, method: PromoteChatMember) -> bool:
        """Выдать права администратора.

        Телеграмные can_* по отдельности MAX не принимает: администратор
        либо есть, либо нет. Точечное снятие прав — это degrade.
        """
        granted = [
            name
            for name in ("can_change_info", "can_delete_messages", "can_invite_users")
            if getattr(method, name, None) is False
        ]
        if granted:
            self._degrade(
                "точечные права администратора",
                f"MAX не разделяет права ({', '.join(granted)}) — выдаются все",
            )
        await self._request(
            "POST",
            f"/chats/{method.chat_id}/members/admins",
            json={"admins": [{"user_id": int(method.user_id)}]},
        )
        return True

    async def _ban_member(self, bot: Bot, method: BanChatMember) -> bool:
        """У MAX это удаление из чата, а не бан: вернуться юзер сможет.

        Проверено на живой группе: обычного участника удаляет (счётчик
        уменьшается, из списка пропадает). На владельце вызов отвечает
        успехом и не делает ничего — предупредить об этом мы не можем,
        различий в ответе нет.
        """
        self._degrade(
            "BanChatMember",
            "у MAX нет бана — участник удаляется и может вернуться по ссылке",
        )
        await self._request(
            "DELETE",
            f"/chats/{method.chat_id}/members",
            params={"user_id": int(method.user_id)},
        )
        return True

    async def _unban_member(self, bot: Bot, method: UnbanChatMember) -> bool:
        """Разбанивать нечего: MAX участника удаляет, а не блокирует."""
        self._degrade(
            "UnbanChatMember", "у MAX нет бана, снимать нечего — вызов пропущен"
        )
        return True
