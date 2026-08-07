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
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    MessageEntity,
    WebAppInfo,
)

from aiogram_max import (
    MarkupPolicy,
    MaxSession,
    UnsupportedByMax,
    UnsupportedPolicy,
    make_bot,
)

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
        self.queries: list[dict[str, str]] = []
        # Сколько раз отправка сообщения ответит «вложение ещё не готово».
        self.fail_messages_times = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        import asyncio
        import json

        try:
            body = json.loads(request.content) if request.content else None
        except json.JSONDecodeError:
            # Заливка файла идёт multipart'ом, а не JSON.
            body = None
        self.requests.append((request.method, request.url.path, body))
        self.queries.append(dict(request.url.params))

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
            if self.fail_messages_times > 0:
                self.fail_messages_times -= 1
                return httpx.Response(
                    400,
                    json={
                        "code": "attachment.not.ready",
                        "message": "errors.process.attachment.file.not.processed",
                    },
                )
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
        if request.url.path == "/uploads":
            # Шаг 1: MAX отдаёт только ссылку, токена здесь ещё нет.
            kind = request.url.params.get("type")
            return httpx.Response(200, json={"url": f"https://up.max.test/{kind}"})
        if request.url.host == "up.max.test":
            # Шаг 2: ответ на заливку отличается по типам — так на живом API.
            if request.url.path.endswith("image"):
                return httpx.Response(
                    200, json={"photos": {"k1": {"token": "img-token"}}}
                )
            return httpx.Response(200, json={"fileId": 1, "token": "file-token"})
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


async def test_typing_action_is_translated_not_dropped() -> None:
    """Индикатор набора у MAX есть, только называется иначе.

    Долгое время здесь стоял no-op с комментарием «у MAX нет typing» —
    заблуждение, унаследованное из чужой реализации. Живой API отвечает
    success на POST /chats/{id}/actions, а вот телеграмное слово «typing»
    не понимает: нужен typing_on.
    """
    fake = FakeMax()
    bot = make_test_bot(fake, unsupported=UnsupportedPolicy.STRICT)

    await bot.send_chat_action(chat_id=42, action="typing")

    assert ("POST", "/chats/42/actions", {"action": "typing_on"}) in fake.requests
    await bot.session.close()


async def test_unknown_action_degrades_instead_of_failing() -> None:
    """Действий вроде «выбирает стикер» у MAX нет — говорим и продолжаем."""
    fake = FakeMax()
    bot = make_test_bot(fake)

    assert await bot.send_chat_action(chat_id=42, action="choose_sticker") is True
    assert not [r for r in fake.requests if r[1].endswith("/actions")]
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


async def test_answer_callback_without_text_is_noop() -> None:
    """Пустой ``callback.answer()`` не должен ходить в MAX.

    У MAX нет телеграмной семантики «просто снять индикатор загрузки»:
    POST /answers с пустым телом отвечает 400 proto.payload — «`message` or
    `notification` required». Отправлять нечего, поэтому вызов пропускаем.
    """
    fake = FakeMax([MESSAGE_CALLBACK])
    bot = make_test_bot(fake)

    router = Router()

    @router.callback_query()
    async def on_click(callback: CallbackQuery) -> None:
        await callback.answer()

    dp = Dispatcher()
    dp.include_router(router)

    updates = await bot.get_updates()
    await dp.feed_update(bot, updates[0])

    assert [r for r in fake.requests if r[1] == "/answers"] == []
    await bot.session.close()


async def test_update_id_comes_from_max_marker() -> None:
    """update_id — позиция события в ленте MAX, а не счётчик в памяти.

    Потребитель (например, свой polling-цикл с дедупом в БД) обязан узнавать
    событие после рестарта процесса. Счётчик с нуля этого не даёт: после
    перезапуска первое же событие снова получает id=1 и выглядит как уже
    обработанное. Позиция берётся из marker'а: MAX отдаёт «следующую
    ожидаемую», значит последнее событие пачки — marker-1.
    """
    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)

    updates = await bot.get_updates()

    assert [u.update_id for u in updates] == [554]  # marker=555 в FakeMax
    await bot.session.close()


async def test_marker_is_readable_after_dropped_update() -> None:
    """Маркер доступен снаружи, даже когда наверх не ушло ни одного события.

    Ради этого свойство и заведено. Потребитель хранит позицию сам и двигает
    её по обработанным событиям. Незнакомый тип (``message_removed``)
    библиотека пропускает: наверх приходит пустой список, двигать позицию
    нечем, следующий запрос возвращает то же событие — опрос встаёт
    намертво. Именно так встал прод. По маркеру потребитель перешагивает
    мёртвое событие.
    """
    fake = FakeMax([{"update_type": "message_removed", "chat_id": 1, "message_id": "x"}])
    bot = make_test_bot(fake)

    updates = await bot.get_updates()

    # bot.session у aiogram типизирован как BaseSession, свойство живёт
    # в нашей MaxSession — сужаем тип, а не давим ошибку через cast.
    session = bot.session
    assert isinstance(session, MaxSession)

    assert updates == []
    assert session.marker == 555
    await session.close()


async def test_marker_is_none_before_first_request() -> None:
    """Пока запросов не было, позиции нет — и врать про неё нельзя."""
    bot = make_test_bot(FakeMax([]))
    session = bot.session
    assert isinstance(session, MaxSession)

    assert session.marker is None
    await session.close()


async def test_offset_from_consumer_becomes_marker() -> None:
    """offset, переданный потребителем, уходит в MAX как marker.

    Так работает докручивание ленты после рестарта: потребитель хранит
    последний update_id, просит offset = id + 1, и это ровно marker MAX.
    """
    fake = FakeMax([])
    bot = make_test_bot(fake)

    await bot.get_updates(offset=7912)

    updates_queries = [q for q in fake.queries if "marker" in q]
    assert updates_queries and updates_queries[0]["marker"] == "7912"
    await bot.session.close()


async def test_update_ids_are_unique_across_batches() -> None:
    """Пачка из нескольких событий получает подряд идущие id, без наложений."""
    fake = FakeMax([MESSAGE_CREATED, MESSAGE_CALLBACK])
    bot = make_test_bot(fake)

    updates = await bot.get_updates()

    assert [u.update_id for u in updates] == [553, 554]
    await bot.session.close()


async def test_markdown_is_converted_to_html_for_max() -> None:
    """Бот шлёт телеграмный MarkdownV2 — в MAX уезжает html.

    Это и есть «удобно из коробки»: код бота не трогали, а разметка
    доехала. Через markdown она бы поехала неправильно — телеграмное
    ``__текст__`` там означает жирный, а не подчёркивание.
    """
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_message(
        chat_id=42,
        text="*жирный* и __подчёркнутый__",
        parse_mode="MarkdownV2",
    )

    sent = next(r for r in fake.requests if r[1] == "/messages")[2]
    assert sent == {
        "text": "<b>жирный</b> и <u>подчёркнутый</u>",
        "format": "html",
    }
    await bot.session.close()


async def test_entities_are_no_longer_dropped_silently() -> None:
    """Разметка через entities раньше терялась без единого предупреждения."""
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_message(
        chat_id=42,
        text="жирный текст",
        entities=[MessageEntity(type="bold", offset=0, length=6)],
    )

    sent = next(r for r in fake.requests if r[1] == "/messages")[2]
    assert sent == {"text": "<b>жирный</b> текст", "format": "html"}
    await bot.session.close()


async def test_html_passes_through_untouched() -> None:
    """HTML MAX понимает сам — трогать нечего."""
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_message(chat_id=42, text="<b>жирный</b>", parse_mode="HTML")

    sent = next(r for r in fake.requests if r[1] == "/messages")[2]
    assert sent == {"text": "<b>жирный</b>", "format": "html"}
    await bot.session.close()


async def test_raw_policy_leaves_text_alone() -> None:
    """MarkupPolicy.RAW — для тех, кто форматирует под MAX сам."""
    fake = FakeMax()
    bot = make_test_bot(fake, markup=MarkupPolicy.RAW)

    await bot.send_message(chat_id=42, text="**жирный**", parse_mode="MarkdownV2")

    sent = next(r for r in fake.requests if r[1] == "/messages")[2]
    assert sent == {"text": "**жирный**", "format": "markdown"}
    await bot.session.close()


async def test_plain_text_gets_no_format() -> None:
    """Без parse_mode и entities форматировать нечего — format не шлём."""
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_message(chat_id=42, text="просто текст")

    sent = next(r for r in fake.requests if r[1] == "/messages")[2]
    assert sent == {"text": "просто текст"}
    await bot.session.close()


async def test_send_photo_uploads_and_attaches_token() -> None:
    """Картинка: слот → заливка → вложение с токеном из ответа заливки.

    Схема выяснена на живом MAX: /uploads отдаёт только ссылку, токен
    приходит после заливки, причём у картинок он спрятан внутри photos.
    """
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_photo(
        chat_id=42,
        photo=BufferedInputFile(b"png-bytes", filename="pic.png"),
        caption="*подпись*",
        parse_mode="MarkdownV2",
    )

    assert any(r[1] == "/uploads" for r in fake.requests)
    sent = next(r for r in fake.requests if r[1] == "/messages")[2]
    assert sent == {
        "attachments": [{"type": "image", "payload": {"token": "img-token"}}],
        "text": "<b>подпись</b>",
        "format": "html",
    }
    await bot.session.close()


async def test_send_document_uses_file_token() -> None:
    """Файлы отдают токен на верхнем уровне, а не внутри photos."""
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_document(
        chat_id=42, document=BufferedInputFile(b"doc", filename="a.txt")
    )

    sent = next(r for r in fake.requests if r[1] == "/messages")[2]
    assert sent == {"attachments": [{"type": "file", "payload": {"token": "file-token"}}]}
    await bot.session.close()


async def test_photo_by_url_skips_upload() -> None:
    """Готовую ссылку MAX принимает как есть — грузить нечего."""
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_photo(chat_id=42, photo="https://example.com/pic.png")

    assert not any(r[1] == "/uploads" for r in fake.requests)
    sent = next(r for r in fake.requests if r[1] == "/messages")[2]
    assert sent == {
        "attachments": [
            {"type": "image", "payload": {"url": "https://example.com/pic.png"}}
        ]
    }
    await bot.session.close()


async def test_edit_reply_markup_replaces_keyboard() -> None:
    """Убрать кнопки после клика — самый частый сценарий правки клавиатуры."""
    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)

    updates = await bot.get_updates()
    message_id = updates[0].message.message_id  # type: ignore[union-attr]

    await bot.edit_message_reply_markup(chat_id=42, message_id=message_id)

    edit = next(r for r in fake.requests if r[0] == "PUT" and r[1] == "/messages")
    assert edit[2] == {"attachments": []}
    await bot.session.close()


async def test_send_photo_waits_until_attachment_is_ready() -> None:
    """MAX обрабатывает залитый файл не мгновенно — ждём и повторяем.

    Сразу после заливки отправка отвечает 400 attachment.not.ready. В
    Telegram такого рукопожатия нет, поэтому боты его не ждут — ждём мы.
    """
    fake = FakeMax()
    fake.fail_messages_times = 2
    bot = make_test_bot(fake)

    await bot.send_photo(chat_id=42, photo=BufferedInputFile(b"png", filename="p.png"))

    sends = [r for r in fake.requests if r[1] == "/messages" and r[0] == "POST"]
    assert len(sends) == 3  # две неудачи и успех
    await bot.session.close()


async def test_gif_animation_goes_to_image_storage() -> None:
    """Телеграмная анимация — это gif или mp4, а у MAX это разные хранилища.

    Видео-хранилище отвечает на gif 415, поэтому решаем по расширению.
    """
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_animation(
        chat_id=42, animation=BufferedInputFile(b"gif", filename="a.gif")
    )

    upload = next(q for q in fake.queries if "type" in q)
    assert upload["type"] == "image"
    await bot.session.close()


async def test_location_puts_coordinates_outside_payload() -> None:
    """У локации координаты лежат на верхнем уровне вложения.

    Остальные типы держат данные в payload, и по аналогии хочется положить
    туда же — живой MAX на это отвечает «latitude cannot be null».
    """
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_location(chat_id=42, latitude=55.75, longitude=37.61)

    sent = next(r for r in fake.requests if r[1] == "/messages")[2]
    assert sent == {
        "attachments": [{"type": "location", "latitude": 55.75, "longitude": 37.61}]
    }
    await bot.session.close()


async def test_media_group_is_one_message_with_many_attachments() -> None:
    """Альбома как сущности у MAX нет — это несколько вложений в сообщении.

    Подпись Telegram берёт у первого элемента, у MAX она становится текстом
    самого сообщения.
    """
    fake = FakeMax()
    bot = make_test_bot(fake)

    await bot.send_media_group(
        chat_id=42,
        media=[
            InputMediaPhoto(
                media=BufferedInputFile(b"a", filename="a.png"), caption="подпись"
            ),
            InputMediaPhoto(media=BufferedInputFile(b"b", filename="b.png")),
        ],
    )

    sent = next(r for r in fake.requests if r[1] == "/messages")[2] or {}
    assert len(sent["attachments"]) == 2
    assert sent["text"] == "подпись"
    await bot.session.close()


async def test_forward_uses_link_not_copy() -> None:
    """Пересылка у MAX — ссылка на исходное сообщение, а не новая копия."""
    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)
    updates = await bot.get_updates()
    message_id = updates[0].message.message_id  # type: ignore[union-attr]

    await bot.forward_message(chat_id=42, from_chat_id=42, message_id=message_id)

    sent = next(r for r in fake.requests if r[1] == "/messages" and r[0] == "POST")[2]
    assert sent == {"link": {"type": "forward", "mid": "mid-abc"}}
    await bot.session.close()


async def test_sticker_needs_max_code_not_telegram_id() -> None:
    """Стикер MAX опознаёт по своему коду; телеграмный file_id ему чужой."""
    fake = FakeMax()
    bot = make_test_bot(fake, unsupported=UnsupportedPolicy.STRICT)

    await bot.send_sticker(chat_id=42, sticker="max-sticker-code")

    sent = next(r for r in fake.requests if r[1] == "/messages")[2] or {}
    assert sent["attachments"] == [
        {"type": "sticker", "payload": {"code": "max-sticker-code"}}
    ]
    await bot.session.close()


async def test_edit_media_uploads_new_file() -> None:
    """Замена вложения — новая заливка: ссылок на старое MAX не отдаёт."""
    fake = FakeMax([MESSAGE_CREATED])
    bot = make_test_bot(fake)
    updates = await bot.get_updates()
    message_id = updates[0].message.message_id  # type: ignore[union-attr]

    await bot.edit_message_media(
        chat_id=42,
        message_id=message_id,
        media=InputMediaPhoto(
            media=BufferedInputFile(b"green", filename="g.png"), caption="стала зелёной"
        ),
    )

    edit = next(r for r in fake.requests if r[0] == "PUT" and r[1] == "/messages")
    # format не проставляется: подпись без parse_mode — обычный текст.
    assert edit[2] == {
        "attachments": [{"type": "image", "payload": {"token": "img-token"}}],
        "text": "стала зелёной",
    }
    await bot.session.close()
