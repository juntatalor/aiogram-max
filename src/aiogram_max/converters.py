"""Перевод между моделями MAX и aiogram.

Это единственное место, где живут знания о форме payload'ов MAX. Всё
остальное работает в терминах aiogram-типов.

Схемы MAX сверены с моделями библиотеки maxapi 1.2.1:
* update: {update_type, timestamp, ...}
* message: {sender: User, recipient: {user_id, chat_id, chat_type},
            timestamp, body: {mid, seq, text, attachments}}
* user: {user_id, first_name, last_name, username, is_bot}
* callback: {timestamp, callback_id, payload, user}
"""

from datetime import UTC, datetime
from typing import Any

from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)

# MAX различает диалог с ботом и групповой чат; Telegram — private/group/channel.
_CHAT_TYPE = {"dialog": "private", "chat": "group", "channel": "channel"}


def chat_type(max_type: str | None) -> str:
    return _CHAT_TYPE.get(max_type or "dialog", "private")


def to_user(raw: dict[str, Any] | None) -> User | None:
    """MAX user → aiogram User."""
    if not raw:
        return None
    return User(
        id=raw["user_id"],
        is_bot=bool(raw.get("is_bot", False)),
        # first_name в Telegram обязателен, в MAX может не прийти.
        first_name=raw.get("first_name") or raw.get("name") or "MAX user",
        last_name=raw.get("last_name"),
        username=raw.get("username"),
    )


def to_chat(recipient: dict[str, Any], sender: dict[str, Any] | None) -> Chat:
    """MAX recipient → aiogram Chat.

    В диалоге MAX может не прислать chat_id — тогда чатом считаем самого
    пользователя, как это делает Telegram в private-чате.
    """
    cid = recipient.get("chat_id") or recipient.get("user_id")
    if cid is None and sender:
        cid = sender.get("user_id")
    return Chat(id=int(cid or 0), type=chat_type(recipient.get("chat_type")))


def to_message(raw: dict[str, Any]) -> Message:
    """MAX message → aiogram Message.

    ``message_id`` берём из body.seq: он целочисленный и монотонный внутри
    чата, тогда как MAX-идентификатор ``mid`` — строка. Соответствие
    seq → mid держит сессия, оно нужно для правки и удаления.
    """
    body = raw.get("body") or {}
    sender = raw.get("sender")
    recipient = raw.get("recipient") or {}
    return Message(
        message_id=int(body.get("seq") or 0),
        date=datetime.fromtimestamp(int(raw.get("timestamp", 0)) / 1000, tz=UTC),
        chat=to_chat(recipient, sender),
        from_user=to_user(sender),
        text=body.get("text"),
    )


def to_update(raw: dict[str, Any], update_id: int) -> Update | None:
    """MAX update → aiogram Update. None — если тип события нам не нужен."""
    kind = raw.get("update_type")

    if kind == "message_created":
        return Update(update_id=update_id, message=to_message(raw["message"]))

    if kind == "message_callback":
        cb = raw["callback"]
        message = raw.get("message")
        return Update(
            update_id=update_id,
            callback_query=CallbackQuery(
                id=cb["callback_id"],
                from_user=to_user(cb["user"]),
                # chat_instance в Telegram обязателен и используется только
                # как ключ группировки; MAX аналога не имеет.
                chat_instance=str(cb.get("callback_id")),
                data=cb.get("payload"),
                message=to_message(message) if message else None,
            ),
        )

    if kind == "bot_started":
        # Нажатие «Начать» в MAX — ближайший аналог /start в Telegram.
        return Update(
            update_id=update_id,
            message=Message(
                message_id=0,
                date=datetime.fromtimestamp(
                    int(raw.get("timestamp", 0)) / 1000, tz=UTC
                ),
                chat=Chat(id=int(raw.get("chat_id") or 0), type="private"),
                from_user=to_user(raw.get("user")),
                text="/start",
            ),
        )

    return None


def keyboard_to_attachment(markup: InlineKeyboardMarkup | None) -> dict[str, Any] | None:
    """aiogram InlineKeyboardMarkup → MAX attachment inline_keyboard.

    Поддерживаем только callback-кнопки и ссылки: у MAX нет switch_inline,
    web_app и прочего телеграмного. Неподдерживаемые кнопки отбрасываем —
    показать пользователю кнопку, которая ничего не делает, хуже.
    """
    if markup is None:
        return None
    rows: list[list[dict[str, Any]]] = []
    for row in markup.inline_keyboard:
        buttons: list[dict[str, Any]] = []
        for btn in row:
            if btn.callback_data is not None:
                buttons.append(
                    {"type": "callback", "text": btn.text, "payload": btn.callback_data}
                )
            elif btn.url is not None:
                buttons.append({"type": "link", "text": btn.text, "url": btn.url})
        if buttons:
            rows.append(buttons)
    if not rows:
        return None
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}
