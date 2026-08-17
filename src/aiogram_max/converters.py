"""Перевод между моделями MAX и aiogram.

Это единственное место, где живут знания о форме payload'ов MAX. Всё
остальное работает в терминах aiogram-типов.

Схемы MAX сверены с моделями библиотеки maxapi 1.2.1:
* update: {update_type, timestamp, ...}
* message: {sender: User, recipient: {user_id, chat_id, chat_type},
            timestamp, body: {mid, seq, text, attachments}, link?: LinkedMessage}
* link: {type: forward|reply, sender: User, chat_id: int,
         message: {mid, seq, text, attachments}} — снято с живого MAX
         2026-08-17, в документации объект не расписан
* user: {user_id, first_name, last_name, username, is_bot}
* callback: {timestamp, callback_id, payload, user}
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from aiogram.enums import MessageOriginType
from aiogram.types import (
    AcceptedGiftTypes,
    CallbackQuery,
    Chat,
    ChatFullInfo,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberOwner,
    Document,
    InlineKeyboardMarkup,
    Message,
    MessageOriginHiddenUser,
    MessageOriginUser,
    PhotoSize,
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

    ``recipient.user_id`` в запасной путь НЕ годится: на живых событиях видно,
    что это получатель конкретного сообщения, а не собеседник. В сообщении от
    юзера боту там лежит id бота, в сообщении бота юзеру — id юзера. Если
    подставить его как chat_id, бот в какой-то момент начнёт отвечать сам
    себе, причём молча. Поэтому запасной путь только через отправителя.
    """
    cid = recipient.get("chat_id")
    if cid is None and sender and not sender.get("is_bot"):
        cid = sender.get("user_id")
    return Chat(id=int(cid or 0), type=chat_type(recipient.get("chat_type")))


def to_attachments(
    raw_attachments: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """MAX attachments → поля aiogram Message (document / photo).

    У MAX нет file_id и метода getFile: вложение приходит готовым URL внутри
    payload. Кладём этот URL в ``file_id`` — сессия отдаёт его обратно как
    ``file_path``, и ``bot.download`` скачивает по прямой ссылке.
    """
    fields: dict[str, Any] = {}
    for att in raw_attachments or []:
        payload = att.get("payload") or {}
        url = payload.get("url")
        if not url:
            continue
        kind = att.get("type")
        if kind == "image" and "photo" not in fields:
            fields["photo"] = [
                PhotoSize(file_id=url, file_unique_id=url, width=0, height=0)
            ]
        elif kind in {"file", "audio", "video"} and "document" not in fields:
            fields["document"] = Document(
                file_id=url,
                file_unique_id=url,
                file_name=att.get("filename"),
                file_size=att.get("size"),
            )
    return fields


def to_linked(link: dict[str, Any] | None, chat: Chat, date: datetime) -> dict[str, Any]:
    """MAX ``message.link`` → поля aiogram про пересылку и ответ.

    MAX кладёт в одно поле два разных случая, различая их по ``type``:

    * ``forward`` — пересланное сообщение. Внешний ``body.text`` при этом
      **пустой**, сам текст лежит в ``link.message.text``. Читая только
      внешний текст, мы получали сообщение ни о чём: ни текста, ни признака
      пересылки, и бот на другой стороне человеку не отвечал.
    * ``reply`` — ответ на сообщение. Внешний текст свой, а в ``link``
      лежит то, на что отвечают.

    Автор оригинала приходит в ``link.sender`` — в Telegram это
    ``forward_origin.sender_user``. Скрытого отправителя MAX не присылает
    вовсе, но на этот случай есть ``MessageOriginHiddenUser``: без него
    событие без ``sender`` уронило бы разбор.
    """
    if not link:
        return {}
    inner = link.get("message") or {}
    author = to_user(link.get("sender"))
    quoted = Message(
        message_id=int(inner.get("seq") or 0),
        date=date,
        chat=chat,
        from_user=author,
        text=inner.get("text"),
        **to_attachments(inner.get("attachments")),
    )

    if link.get("type") == "forward":
        origin = (
            MessageOriginUser(type=MessageOriginType.USER, date=date, sender_user=author)
            if author is not None
            else MessageOriginHiddenUser(
                type=MessageOriginType.HIDDEN_USER,
                date=date,
                sender_user_name="MAX user",
            )
        )
        return {"forward_origin": origin, "forward_text": inner.get("text")}
    if link.get("type") == "reply":
        return {"reply_to_message": quoted}
    # Неизвестный вид связи: молча терять нельзя, но и падать не на чем —
    # отдаём пустое, сообщение доедет как обычное.
    return {}


def to_message(raw: dict[str, Any]) -> Message:
    """MAX message → aiogram Message.

    ``message_id`` берём из body.seq: он целочисленный и монотонный внутри
    чата, тогда как MAX-идентификатор ``mid`` — строка. Соответствие
    seq → mid держит сессия, оно нужно для правки и удаления.
    """
    body = raw.get("body") or {}
    sender = raw.get("sender")
    recipient = raw.get("recipient") or {}
    chat = to_chat(recipient, sender)
    date = datetime.fromtimestamp(int(raw.get("timestamp", 0)) / 1000, tz=UTC)

    linked = to_linked(raw.get("link"), chat, date)
    # У пересланного сообщения текст только внутри link — снаружи пустая
    # строка, и она обязана уступить.
    forwarded_text = linked.pop("forward_text", None)
    text = body.get("text") or forwarded_text

    return Message(
        message_id=int(body.get("seq") or 0),
        date=date,
        chat=chat,
        from_user=to_user(sender),
        text=text,
        **to_attachments(body.get("attachments")),
        **linked,
    )


def to_update(raw: dict[str, Any], update_id: int) -> Update | None:
    """MAX update → aiogram Update. None — если тип события нам не нужен."""
    kind = raw.get("update_type")

    if kind == "message_created":
        message = raw.get("message")
        if message is None:
            # MAX умеет прислать message_created без самого сообщения.
            # Падение здесь роняет весь get_updates, а значит и опрос целиком:
            # событие лежит в очереди и валит каждый следующий круг, пока
            # человек ждёт ответа. Пропускаем, как любой неизвестный тип.
            return None
        return Update(update_id=update_id, message=to_message(message))

    if kind == "message_callback":
        cb = raw["callback"]
        message = raw.get("message")
        clicker = to_user(cb.get("user"))
        if clicker is None:
            # Кто нажал — обязательное поле CallbackQuery в aiogram, и без него
            # событие всё равно некуда роутить. Пропускаем как неизвестный тип,
            # а не падаем: иначе один кривой апдейт застопорит весь polling.
            return None
        return Update(
            update_id=update_id,
            callback_query=CallbackQuery(
                id=cb["callback_id"],
                from_user=clicker,
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
                date=datetime.fromtimestamp(int(raw.get("timestamp", 0)) / 1000, tz=UTC),
                chat=Chat(id=int(raw.get("chat_id") or 0), type="private"),
                from_user=to_user(raw.get("user")),
                text="/start",
            ),
        )

    return None


# Telegram HTML/Markdown → MAX format. MarkdownV2 у MAX аналога не имеет,
# ближайшее — markdown (CommonMark), о расхождении предупреждает вызывающий.
_PARSE_MODE = {"HTML": "html", "Markdown": "markdown", "MarkdownV2": "markdown"}


def parse_mode_to_format(parse_mode: str | None) -> str | None:
    """aiogram parse_mode → MAX format."""
    if parse_mode is None:
        return None
    return _PARSE_MODE.get(str(parse_mode))


def keyboard_to_attachment(
    markup: InlineKeyboardMarkup | None,
    degrade: Callable[[str, str], None] | None = None,
) -> dict[str, Any] | None:
    """aiogram InlineKeyboardMarkup → MAX attachment inline_keyboard.

    MAX знает только callback-кнопки и ссылки. Кнопку, которой нет аналога,
    отбрасываем — показать пользователю кнопку, которая ничего не делает,
    хуже. Но не молча: сообщаем через ``degrade``.
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
            elif degrade is not None:
                kind = next(
                    (
                        name
                        for name in (
                            "web_app",
                            "login_url",
                            "switch_inline_query",
                            "switch_inline_query_current_chat",
                            "callback_game",
                            "pay",
                            "copy_text",
                        )
                        if getattr(btn, name, None) is not None
                    ),
                    "кнопка неизвестного типа",
                )
                degrade(f"InlineKeyboardButton.{kind}", f"текст кнопки: {btn.text!r}")
        if buttons:
            rows.append(buttons)
    if not rows:
        return None
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


# MAX-тип чата → телеграмный. «dialog» — личка, «chat» — группа.
_CHAT_TYPES = {"dialog": "private", "chat": "group", "channel": "channel"}


def to_chat_full_info(raw: dict[str, Any]) -> ChatFullInfo:
    """MAX chat → aiogram ChatFullInfo (её возвращает getChat)."""
    return ChatFullInfo(
        id=raw["chat_id"],
        type=_CHAT_TYPES.get(str(raw.get("type")), "group"),
        title=raw.get("title"),
        description=raw.get("description"),
        invite_link=raw.get("link"),
        accent_color_id=0,
        max_reaction_count=0,
        # Полей про подарки у MAX нет; aiogram требует объект — отдаём пустой.
        accepted_gift_types=AcceptedGiftTypes(
            unlimited_gifts=False,
            limited_gifts=False,
            unique_gifts=False,
            premium_subscription=False,
            gifts_from_channels=False,
        ),
    )


def to_chat_member(
    raw: dict[str, Any],
) -> ChatMemberOwner | ChatMemberAdministrator | ChatMemberMember:
    """MAX participant → aiogram ChatMember.

    Прав администратора MAX по отдельности не отдаёт — только флаг is_admin.
    Поэтому телеграмные can_* проставляем в False: соврать «может всё»
    опаснее, чем занизить, бот на это ориентируется в проверках доступа.
    """
    user = to_user(raw)
    assert user is not None
    if raw.get("is_owner"):
        return ChatMemberOwner(user=user, is_anonymous=False)
    if raw.get("is_admin"):
        return ChatMemberAdministrator(
            user=user,
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
        )
    return ChatMemberMember(user=user)
