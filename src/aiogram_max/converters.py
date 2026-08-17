"""Перевод между моделями MAX и aiogram.

Это единственное место, где живут знания о форме payload'ов MAX. Всё
остальное работает в терминах aiogram-типов.

Форма событий MAX описана моделями в ``schemas.py`` — там же объяснено,
почему они принимают всё подряд. Здесь остаётся только перевод: из модели
MAX в тип aiogram.
"""

import logging
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
from pydantic import ValidationError

from aiogram_max.schemas import (
    AttachmentType,
    ChatType,
    LinkType,
    MaxAttachment,
    MaxBody,
    MaxLink,
    MaxMessage,
    MaxRecipient,
    MaxUpdate,
    MaxUser,
    UpdateType,
)

logger = logging.getLogger(__name__)

# MAX различает диалог с ботом и групповой чат; Telegram — private/group/channel.
_CHAT_TYPE: dict[str, str] = {
    ChatType.DIALOG: "private",
    ChatType.CHAT: "group",
    ChatType.CHANNEL: "channel",
}


def chat_type(max_type: str | None) -> str:
    """MAX chat_type → тип чата Telegram. Незнакомый считаем диалогом."""
    return _CHAT_TYPE.get(max_type or ChatType.DIALOG, "private")


def to_user(raw: dict[str, Any] | MaxUser | None) -> User | None:
    """MAX user → aiogram User. None — человека в событии нет.

    Словарь на входе принимается ради вызывающих снаружи: сессия зовёт это
    на ответе ``GET /me``, где модели события нет.
    """
    user = MaxUser.model_validate(raw) if isinstance(raw, dict) else raw
    if user is None or user.user_id is None:
        return None
    return User(
        id=user.user_id,
        is_bot=user.is_bot,
        # first_name в Telegram обязателен, в MAX может не прийти.
        first_name=user.first_name or user.name or "MAX user",
        last_name=user.last_name,
        username=user.username,
    )


def to_chat(recipient: MaxRecipient | None, sender: MaxUser | None) -> Chat:
    """MAX recipient → aiogram Chat.

    ``recipient.user_id`` в запасной путь НЕ годится: на живых событиях видно,
    что это получатель конкретного сообщения, а не собеседник. В сообщении от
    юзера боту там лежит id бота, в сообщении бота юзеру — id юзера. Если
    подставить его как chat_id, бот в какой-то момент начнёт отвечать сам
    себе, причём молча. Поэтому запасной путь только через отправителя.
    """
    recipient = recipient or MaxRecipient()
    cid = recipient.chat_id
    if cid is None and sender is not None and not sender.is_bot:
        cid = sender.user_id
    return Chat(id=int(cid or 0), type=chat_type(recipient.chat_type))


def to_attachments(attachments: list[MaxAttachment] | None) -> dict[str, Any]:
    """MAX attachments → поля aiogram Message (document / photo).

    У MAX нет file_id и метода getFile: вложение приходит готовым URL внутри
    payload. Кладём этот URL в ``file_id`` — сессия отдаёт его обратно как
    ``file_path``, и ``bot.download`` скачивает по прямой ссылке.
    """
    fields: dict[str, Any] = {}
    for att in attachments or []:
        url = att.payload.url if att.payload else None
        if not url:
            continue
        if att.type == AttachmentType.IMAGE and "photo" not in fields:
            fields["photo"] = [
                PhotoSize(file_id=url, file_unique_id=url, width=0, height=0)
            ]
        elif (
            att.type in {AttachmentType.FILE, AttachmentType.AUDIO, AttachmentType.VIDEO}
            and "document" not in fields
        ):
            fields["document"] = Document(
                file_id=url,
                file_unique_id=url,
                file_name=att.filename,
                file_size=att.size,
            )
    return fields


def to_linked(link: MaxLink | None, chat: Chat, date: datetime) -> dict[str, Any]:
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
    if link is None:
        return {}
    inner = link.message or MaxBody()
    author = to_user(link.sender)

    if link.type == LinkType.FORWARD:
        origin = (
            MessageOriginUser(type=MessageOriginType.USER, date=date, sender_user=author)
            if author is not None
            else MessageOriginHiddenUser(
                type=MessageOriginType.HIDDEN_USER,
                date=date,
                sender_user_name="MAX user",
            )
        )
        return {"forward_origin": origin, "forward_text": inner.text}
    if link.type == LinkType.REPLY:
        return {
            "reply_to_message": Message(
                message_id=int(inner.seq or 0),
                date=date,
                chat=chat,
                from_user=author,
                text=inner.text,
                **to_attachments(inner.attachments),
            )
        }
    # Незнакомый вид связи: молча терять нельзя, но и падать не на чем —
    # отдаём пустое, сообщение доедет как обычное.
    logger.debug("MAX link неизвестного вида: %s", link.type)
    return {}


def to_message(raw: dict[str, Any] | MaxMessage) -> Message:
    """MAX message → aiogram Message.

    ``message_id`` берём из body.seq: он целочисленный и монотонный внутри
    чата, тогда как MAX-идентификатор ``mid`` — строка. Соответствие
    seq → mid держит сессия, оно нужно для правки и удаления.
    """
    message = MaxMessage.model_validate(raw) if isinstance(raw, dict) else raw
    body = message.body or MaxBody()
    chat = to_chat(message.recipient, message.sender)
    date = datetime.fromtimestamp(int(message.timestamp or 0) / 1000, tz=UTC)

    linked = to_linked(message.link, chat, date)
    # У пересланного сообщения текст только внутри link — снаружи пустая
    # строка, и она обязана уступить.
    forwarded_text = linked.pop("forward_text", None)

    return Message(
        message_id=int(body.seq or 0),
        date=date,
        chat=chat,
        from_user=to_user(message.sender),
        text=body.text or forwarded_text,
        **to_attachments(body.attachments),
        **linked,
    )


def to_update(raw: dict[str, Any], update_id: int) -> Update | None:
    """MAX update → aiogram Update. None — событие нам не нужно или не разобралось.

    Здесь же стоит единственная в библиотеке защита от неожиданного payload:
    разбор одного события изолирован, и его отказ стоит ровно этого события.
    Без изоляции падение уронило бы всю пачку ``get_updates``: позиция не
    сдвинулась бы, та же пачка пришла бы следующим кругом и упала снова —
    опрос встаёт навсегда, а человек ждёт ответа. Так бот уже молчал сорок
    пять минут из-за ``message_created`` без тела.
    """
    try:
        event = MaxUpdate.model_validate(raw)
    except ValidationError as e:
        logger.warning(
            "MAX update не разобрался, пропускаем: тип=%s ошибка=%s",
            raw.get("update_type") if isinstance(raw, dict) else "?",
            e,
        )
        return None

    if event.update_type == UpdateType.MESSAGE_CREATED:
        if event.message is None:
            # MAX умеет прислать message_created без самого сообщения.
            logger.debug("MAX message_created без сообщения, пропускаем")
            return None
        return Update(update_id=update_id, message=to_message(event.message))

    if event.update_type == UpdateType.MESSAGE_CALLBACK:
        callback = event.callback
        clicker = to_user(callback.user) if callback else None
        if callback is None or callback.callback_id is None or clicker is None:
            # Кто нажал и id нажатия — обязательные поля CallbackQuery в
            # aiogram, и без них событие всё равно некуда роутить.
            logger.debug("MAX callback без нажавшего или без id, пропускаем")
            return None
        return Update(
            update_id=update_id,
            callback_query=CallbackQuery(
                id=callback.callback_id,
                from_user=clicker,
                # chat_instance в Telegram обязателен и используется только
                # как ключ группировки; MAX аналога не имеет.
                chat_instance=callback.callback_id,
                data=callback.payload,
                message=to_message(event.message) if event.message else None,
            ),
        )

    if event.update_type == UpdateType.BOT_STARTED:
        # Нажатие «Начать» в MAX — ближайший аналог /start в Telegram.
        return Update(
            update_id=update_id,
            message=Message(
                message_id=0,
                date=datetime.fromtimestamp(int(event.timestamp or 0) / 1000, tz=UTC),
                chat=Chat(id=int(event.chat_id or 0), type="private"),
                from_user=to_user(event.user),
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


def to_chat_full_info(raw: dict[str, Any]) -> ChatFullInfo:
    """MAX chat → aiogram ChatFullInfo (её возвращает getChat)."""
    return ChatFullInfo(
        id=raw["chat_id"],
        type=chat_type(raw.get("type")),
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
