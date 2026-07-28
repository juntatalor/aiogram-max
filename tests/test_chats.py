"""Групповые методы: чат, участники, админы, пины, настройки.

Payload'ы сняты с живого MAX: бот сидел в тестовой группе, ответы записаны
как есть. Отдельно зафиксировано, что список админов доступен только самим
администраторам — на этом легко построить неверное ожидание.
"""

from typing import Any

import httpx
import pytest
from aiogram import Bot
from aiogram.types import BufferedInputFile, ChatMemberMember, ChatMemberOwner

from aiogram_max import (
    MaxApiError,
    UnsupportedByMax,
    UnsupportedPolicy,
    make_bot,
)

GROUP = -77360344505750

# Живой ответ GET /chats/{id}.
CHAT = {
    "chat_id": GROUP,
    "type": "chat",
    "status": "active",
    "title": "Группа с талантом",
    "participants_count": 2,
    "is_public": False,
    "link": "https://max.ru/join/vcyl7xBhfIaSDj99QmP3RM5xqVyMQckvsppYXNH1S0E",
}

BOT_MEMBER = {
    "user_id": 277639678,
    "first_name": "Умная система Талант",
    "username": "id662007045961_bot",
    "is_bot": True,
    "is_owner": False,
    "is_admin": False,
}
HUMAN_OWNER = {
    "user_id": 133219938,
    "first_name": "Сергей",
    "is_bot": False,
    "is_owner": True,
    "is_admin": True,
}


class FakeChats:
    """Мини-MAX для групповых ручек."""

    def __init__(self, *, bot_is_admin: bool = False) -> None:
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.bot_is_admin = bot_is_admin

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        import json

        try:
            body = json.loads(request.content) if request.content else None
        except json.JSONDecodeError:
            body = None  # multipart-заливка
        path = request.url.path
        self.requests.append((request.method, path, body))

        if path == f"/chats/{GROUP}" and request.method == "GET":
            return httpx.Response(200, json=CHAT)
        if path == f"/chats/{GROUP}/members/admins":
            if not self.bot_is_admin:
                # Ровно так отвечает живой MAX боту без прав.
                return httpx.Response(
                    403,
                    json={
                        "code": "chat.denied",
                        "message": "Method is available only for chat administrator",
                    },
                )
            return httpx.Response(200, json={"members": [HUMAN_OWNER]})
        if path == f"/chats/{GROUP}/members/me":
            return httpx.Response(200, json=BOT_MEMBER)
        if path == f"/chats/{GROUP}/members":
            return httpx.Response(200, json={"members": [BOT_MEMBER, HUMAN_OWNER]})
        if path == "/uploads":
            return httpx.Response(200, json={"url": "https://up.max.test/image"})
        if request.url.host == "up.max.test":
            return httpx.Response(200, json={"photos": {"k": {"token": "img-token"}}})
        if path == f"/chats/{GROUP}/pin":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})


def make_chat_bot(fake: FakeChats, **kwargs: Any) -> Bot:
    client = httpx.AsyncClient(transport=fake.transport())
    return make_bot("max-token", bot_id=277639678, client=client, **kwargs)


async def test_get_chat_maps_type_and_link() -> None:
    """MAX-тип «chat» — это телеграмная группа, ссылка-приглашение переносится."""
    fake = FakeChats()
    bot = make_chat_bot(fake)

    chat = await bot.get_chat(GROUP)

    assert chat.id == GROUP
    assert chat.type == "group"
    assert chat.title == "Группа с талантом"
    assert chat.invite_link == CHAT["link"]
    await bot.session.close()


async def test_member_count_comes_from_chat() -> None:
    """Отдельной ручки со счётчиком нет — берём поле из описания чата."""
    fake = FakeChats()
    bot = make_chat_bot(fake)

    assert await bot.get_chat_member_count(GROUP) == 2
    await bot.session.close()


async def test_get_chat_member_uses_me_shortcut() -> None:
    """Про себя MAX отвечает отдельной ручкой — списка листать не надо."""
    fake = FakeChats()
    bot = make_chat_bot(fake)

    member = await bot.get_chat_member(GROUP, 277639678)

    assert isinstance(member, ChatMemberMember)
    assert member.user.username == "id662007045961_bot"
    assert any(r[1].endswith("/members/me") for r in fake.requests)
    await bot.session.close()


async def test_get_chat_member_scans_list_for_others() -> None:
    """Выборки по user_id у MAX нет, поэтому чужого участника ищем в списке."""
    fake = FakeChats()
    bot = make_chat_bot(fake)

    member = await bot.get_chat_member(GROUP, 133219938)

    assert isinstance(member, ChatMemberOwner)
    assert member.user.first_name == "Сергей"
    await bot.session.close()


async def test_admins_require_admin_rights() -> None:
    """Боту без прав MAX отвечает chat.denied — это не наша ошибка."""
    fake = FakeChats(bot_is_admin=False)
    bot = make_chat_bot(fake)

    with pytest.raises(MaxApiError, match=r"chat\.denied"):
        await bot.get_chat_administrators(GROUP)
    await bot.session.close()


async def test_admins_returned_when_bot_is_admin() -> None:
    fake = FakeChats(bot_is_admin=True)
    bot = make_chat_bot(fake)

    admins = await bot.get_chat_administrators(GROUP)

    assert len(admins) == 1
    assert isinstance(admins[0], ChatMemberOwner)
    await bot.session.close()


async def test_admin_rights_are_not_split() -> None:
    """MAX не знает отдельных прав: администратор либо есть, либо нет.

    Просьбу выдать урезанные права выполняем целиком, но говорим об этом:
    молчание тут означало бы «дал меньше, чем просили» без следа в логах.
    """
    fake = FakeChats()
    bot = make_chat_bot(fake, unsupported=UnsupportedPolicy.STRICT)

    with pytest.raises(UnsupportedByMax, match="права"):
        await bot.promote_chat_member(GROUP, 133219938, can_change_info=False)
    await bot.session.close()


async def test_ban_is_removal_not_ban() -> None:
    """У MAX бана нет: участник удаляется и может вернуться по ссылке."""
    fake = FakeChats(bot_is_admin=True)
    bot = make_chat_bot(fake, unsupported=UnsupportedPolicy.STRICT)

    with pytest.raises(UnsupportedByMax, match="нет бана"):
        await bot.ban_chat_member(GROUP, 133219938)
    await bot.session.close()


async def test_unpin_all_is_same_call_as_unpin() -> None:
    """У MAX закреплено одно сообщение, поэтому «снять все» — тот же вызов."""
    fake = FakeChats(bot_is_admin=True)
    bot = make_chat_bot(fake)

    await bot.unpin_all_chat_messages(GROUP)

    assert ("DELETE", f"/chats/{GROUP}/pin", None) in fake.requests
    await bot.session.close()


async def test_set_title_patches_chat() -> None:
    fake = FakeChats(bot_is_admin=True)
    bot = make_chat_bot(fake)

    await bot.set_chat_title(GROUP, "Новое название")

    patches = [r for r in fake.requests if r[0] == "PATCH"]
    assert patches[0][2] == {"title": "Новое название"}
    await bot.session.close()


async def test_description_is_not_supported_by_max() -> None:
    """Описание чата MAX боту менять не даёт — молчать об этом нельзя.

    На живом API PATCH с description отвечает 200 и отдаёт чат, но поле не
    меняется. Успешный ответ здесь означал бы «сделано», а сделано не было.
    """
    fake = FakeChats(bot_is_admin=True)
    bot = make_chat_bot(fake, unsupported=UnsupportedPolicy.STRICT)

    with pytest.raises(UnsupportedByMax, match="SetChatDescription"):
        await bot.set_chat_description(GROUP, "Новое описание")
    assert not [r for r in fake.requests if r[0] == "PATCH"]
    await bot.session.close()


async def test_ban_removes_member_from_chat() -> None:
    """Удаление обычного участника доходит до MAX как DELETE /members.

    На живой группе проверено, что участник действительно пропадает:
    счётчик уменьшился с трёх до двух. С владельцем иначе — MAX отвечает
    успехом и никого не удаляет, но по ответу это неотличимо.
    """
    fake = FakeChats(bot_is_admin=True)
    bot = make_chat_bot(fake)

    await bot.ban_chat_member(GROUP, 134510822)

    assert ("DELETE", f"/chats/{GROUP}/members", None) in fake.requests
    await bot.session.close()


async def test_set_chat_photo_uploads_and_patches_icon() -> None:
    """Иконка чата ставится токеном загруженной картинки.

    Ссылку вместо токена MAX не принимает — отвечает internal.error, так
    что путь через загрузку здесь единственный.
    """
    fake = FakeChats(bot_is_admin=True)
    bot = make_chat_bot(fake)

    await bot.set_chat_photo(
        chat_id=GROUP, photo=BufferedInputFile(b"png", filename="icon.png")
    )

    patch = next(r for r in fake.requests if r[0] == "PATCH")
    assert patch[2] == {"icon": {"token": "img-token"}}
    await bot.session.close()
