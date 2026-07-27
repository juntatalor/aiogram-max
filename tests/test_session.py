"""Проверка гипотезы: нетронутый aiogram-бот работает поверх MAX.

Ключевой момент — в этих тестах нет ни одного импорта из aiogram_max в коде
самого бота. Роутер, фильтры, типы и FSM берутся из aiogram как есть; всё,
что мы подменили, — транспорт.
"""

from typing import Any

import httpx
import pytest
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.methods import SendPoll
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from aiogram_max import UnsupportedByMax, UnsupportedPolicy, make_bot

# --- фейковый MAX ---------------------------------------------------------

MESSAGE_CREATED = {
    "update_type": "message_created",
    "timestamp": 1769500000000,
    "message": {
        "sender": {"user_id": 777, "first_name": "Сергей", "is_bot": False},
        "recipient": {"chat_id": 42, "chat_type": "dialog"},
        "timestamp": 1769500000000,
        "body": {"mid": "mid-abc", "seq": 11, "text": "/start"},
    },
}

MESSAGE_CALLBACK = {
    "update_type": "message_callback",
    "timestamp": 1769500001000,
    "callback": {
        "timestamp": 1769500001000,
        "callback_id": "cb-1",
        "payload": "accept:42",
        "user": {"user_id": 777, "first_name": "Сергей", "is_bot": False},
    },
    "message": {
        "sender": {"user_id": 777, "first_name": "Сергей", "is_bot": False},
        "recipient": {"chat_id": 42, "chat_type": "dialog"},
        "timestamp": 1769500001000,
        "body": {"mid": "mid-abc", "seq": 11, "text": "меню"},
    },
}


class FakeMax:
    """Мини-сервер MAX: отдаёт заготовленные события, пишет запросы."""

    def __init__(self, updates: list[dict[str, Any]] | None = None) -> None:
        self.updates = updates or []
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        import asyncio
        import json

        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))

        if request.url.path == "/updates":
            batch, self.updates = self.updates, []
            if not batch:
                # Настоящий long polling держит соединение, пока нет событий.
                # Без паузы MockTransport отвечает мгновенно, polling-цикл
                # aiogram ни разу не отдаёт управление планировщику и задачи
                # обработки апдейтов не успевают стартовать.
                await asyncio.sleep(0.05)
            return httpx.Response(200, json={"updates": batch, "marker": 555})
        if request.url.path == "/messages" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "message": {
                        "sender": {"user_id": 1, "first_name": "bot", "is_bot": True},
                        "recipient": {"chat_id": 42, "chat_type": "dialog"},
                        "timestamp": 1769500002000,
                        "body": {"mid": "mid-out", "seq": 12, "text": "ok"},
                    }
                },
            )
        if request.url.host == "files.max.ru":
            return httpx.Response(200, content=b"docx-bytes")
        if request.url.path == "/me":
            return httpx.Response(
                200, json={"user_id": 1, "first_name": "pocherk", "is_bot": True}
            )
        return httpx.Response(200, json={})


def make_test_bot(fake: FakeMax, **kwargs: Any) -> Bot:
    client = httpx.AsyncClient(transport=fake.transport())
    return make_bot("max-token", client=client, **kwargs)


# --- тесты ----------------------------------------------------------------


async def test_get_updates_returns_aiogram_updates() -> None:
    """MAX-событие превращается в валидный aiogram Update."""
    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)

    updates = await bot.get_updates()

    assert len(updates) == 1
    message = updates[0].message
    assert message is not None
    assert message.text == "/start"
    assert message.chat.id == 42
    assert message.chat.type == "private"
    assert message.from_user is not None
    assert message.from_user.first_name == "Сергей"
    await bot.session.close()


async def test_dispatcher_routes_message_to_handler() -> None:
    """Роутер и фильтр Command — стандартные aiogram, и они срабатывают."""
    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)
    seen: list[str] = []

    router = Router()

    @router.message(Command("start"))
    async def on_start(message: Message) -> None:
        seen.append(message.text or "")
        await message.answer(
            "Привет из MAX",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Принять", callback_data="accept:42")]
                ]
            ),
        )

    dp = Dispatcher()
    dp.include_router(router)

    updates = await bot.get_updates()
    await dp.feed_update(bot, updates[0])

    assert seen == ["/start"]
    sent = [r for r in fake.requests if r[1] == "/messages" and r[0] == "POST"]
    assert len(sent) == 1
    _, _, body = sent[0]
    assert body is not None
    assert body["text"] == "Привет из MAX"
    assert body["attachments"] == [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [{"type": "callback", "text": "Принять", "payload": "accept:42"}]
                ]
            },
        }
    ]
    await bot.session.close()


async def test_callback_query_flows_through_aiogram() -> None:
    """Клик по кнопке доезжает до callback-хендлера с магическим фильтром F."""
    fake = FakeMax([MESSAGE_CALLBACK])
    bot = make_test_bot(fake)
    seen: list[str] = []

    router = Router()

    @router.callback_query(F.data.startswith("accept:"))
    async def on_accept(callback: CallbackQuery) -> None:
        seen.append(callback.data or "")
        await callback.answer("Принято")

    dp = Dispatcher()
    dp.include_router(router)

    updates = await bot.get_updates()
    await dp.feed_update(bot, updates[0])

    assert seen == ["accept:42"]
    answers = [r for r in fake.requests if r[1] == "/answers"]
    assert len(answers) == 1
    assert answers[0][2] == {"notification": "Принято"}
    await bot.session.close()


async def test_fsm_state_survives_platform_swap() -> None:
    """FSM aiogram работает поверх MAX без изменений."""

    class Form(StatesGroup):
        waiting_idea = State()

    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)
    states: list[str | None] = []

    router = Router()

    @router.message(Command("start"))
    async def on_start(message: Message, state: FSMContext) -> None:
        await state.set_state(Form.waiting_idea)
        states.append(await state.get_state())

    dp = Dispatcher()
    dp.include_router(router)

    updates = await bot.get_updates()
    await dp.feed_update(bot, updates[0])

    assert states == ["Form:waiting_idea"]
    await bot.session.close()


async def test_unsupported_method_raises_in_strict_mode() -> None:
    """Метода нет у MAX — в строгом режиме падаем громко и по имени."""
    fake = FakeMax()
    bot = make_test_bot(fake, unsupported=UnsupportedPolicy.STRICT)

    with pytest.raises(UnsupportedByMax) as exc:
        await bot(SendPoll(chat_id=42, question="?", options=["a", "b"]))

    assert "SendPoll" in str(exc.value)
    await bot.session.close()


async def test_unsupported_method_is_skipped_by_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """По умолчанию вызов пропускается с предупреждением, бот живёт дальше."""
    fake = FakeMax()
    bot = make_test_bot(fake)

    with caplog.at_level("WARNING"):
        assert await bot(SendPoll(chat_id=42, question="?", options=["a", "b"])) is None

    assert "SendPoll" in caplog.text
    await bot.session.close()


async def test_dropped_button_warns_but_keeps_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Кнопки без аналога в MAX выбрасываются — но не молча."""
    fake = FakeMax()
    bot = make_test_bot(fake)

    with caplog.at_level("WARNING"):
        await bot.send_message(
            chat_id=42,
            text="меню",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Ок", callback_data="ok"),
                        InlineKeyboardButton(
                            text="Мини-апп", web_app=WebAppInfo(url="https://e.com")
                        ),
                    ]
                ]
            ),
        )

    assert "web_app" in caplog.text
    assert "Мини-апп" in caplog.text
    body = next(r[2] for r in fake.requests if r[1] == "/messages")
    assert body is not None
    buttons = body["attachments"][0]["payload"]["buttons"]
    assert buttons == [[{"type": "callback", "text": "Ок", "payload": "ok"}]]
    await bot.session.close()


async def test_dropped_button_raises_in_strict_mode() -> None:
    """В строгом режиме потеря кнопки — ошибка, а не предупреждение."""
    fake = FakeMax()
    bot = make_test_bot(fake, unsupported=UnsupportedPolicy.STRICT)

    with pytest.raises(UnsupportedByMax) as exc:
        await bot.send_message(
            chat_id=42,
            text="меню",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Мини-апп", web_app=WebAppInfo(url="https://e.com")
                        )
                    ]
                ]
            ),
        )

    assert "web_app" in str(exc.value)
    await bot.session.close()


async def test_supported_params_are_mapped_not_dropped() -> None:
    """parse_mode, disable_notification и reply переводятся в поля MAX."""
    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)

    await bot.get_updates()  # запомнили seq=11 → mid-abc
    await bot.send_message(
        chat_id=42,
        text="<b>жирный</b>",
        parse_mode="HTML",
        disable_notification=True,
        reply_to_message_id=11,
    )

    body = next(r[2] for r in fake.requests if r[1] == "/messages")
    assert body is not None
    assert body["format"] == "html"
    assert body["notify"] is False
    assert body["link"] == {"type": "reply", "mid": "mid-abc"}
    await bot.session.close()


async def test_typing_indicator_is_silent_noop() -> None:
    """У MAX нет typing — но это не повод ронять бота даже в строгом режиме."""
    fake = FakeMax()
    bot = make_test_bot(fake, unsupported=UnsupportedPolicy.STRICT)

    assert await bot.send_chat_action(chat_id=42, action="typing") is True
    assert fake.requests == []
    await bot.session.close()


async def test_edit_message_uses_max_mid() -> None:
    """aiogram правит по int-id, MAX — по строковому mid; сессия их сшивает."""
    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)

    await bot.get_updates()  # запомнили seq=11 → mid-abc
    await bot.edit_message_text(chat_id=42, message_id=11, text="правка")

    edits = [r for r in fake.requests if r[0] == "PUT"]
    assert len(edits) == 1
    assert edits[0][2] == {"text": "правка"}
    await bot.session.close()


async def test_get_me_returns_aiogram_user() -> None:
    fake = FakeMax()
    bot = make_test_bot(fake)

    me = await bot.get_me()

    assert me.id == 1
    assert me.is_bot is True
    await bot.session.close()


MESSAGE_WITH_FILE = {
    "update_type": "message_created",
    "timestamp": 1769500003000,
    "message": {
        "sender": {"user_id": 777, "first_name": "Сергей", "is_bot": False},
        "recipient": {"chat_id": 42, "chat_type": "dialog"},
        "timestamp": 1769500003000,
        "body": {
            "mid": "mid-file",
            "seq": 13,
            "text": "вот примеры постов",
            "attachments": [
                {
                    "type": "file",
                    "filename": "posts.docx",
                    "size": 2048,
                    "payload": {"url": "https://files.max.ru/posts.docx", "token": "t"},
                }
            ],
        },
    },
}


async def test_file_attachment_is_downloadable() -> None:
    """У MAX нет file_id и getFile — ссылка приходит с сообщением.

    Проверяем весь путь: вложение доехало до aiogram Document, bot.download
    сходил по прямой ссылке MAX и вернул содержимое.
    """
    fake = FakeMax([MESSAGE_WITH_FILE])
    bot = make_test_bot(fake)

    updates = await bot.get_updates()
    message = updates[0].message
    assert message is not None
    document = message.document
    assert document is not None
    assert document.file_name == "posts.docx"
    assert document.file_size == 2048

    buffer = await bot.download(document)
    assert buffer is not None
    assert buffer.read() == b"docx-bytes"
    assert ("GET", "/posts.docx", None) in fake.requests
    await bot.session.close()
