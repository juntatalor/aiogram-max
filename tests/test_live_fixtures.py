"""Тесты на payload'ах, снятых с живого MAX Bot API.

Отличие от test_session: там события выдуманы мной по моделям maxapi, здесь —
дословно то, что MAX прислал на реальные действия пользователя (обезличено).
Именно эти тесты ловят расхождения между «как я прочитал документацию» и
«как оно на самом деле».
"""

import json
import pathlib

from aiogram.types import MessageOriginUser

from aiogram_max import converters

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "live_max_updates.json").read_text(
        encoding="utf-8"
    )
)


def test_live_message_created() -> None:
    update = converters.to_update(FIXTURES["message_created"], 1)

    assert update is not None
    message = update.message
    assert message is not None
    assert message.text == "Тест"
    # chat_id в диалоге MAX присылает — проверено живьём.
    assert message.chat.id == 472107880
    assert message.chat.type == "private"
    assert message.from_user is not None
    assert message.from_user.id == 212843158
    assert message.from_user.is_bot is False


def test_live_chat_id_is_not_taken_from_recipient_user_id() -> None:
    """recipient.user_id — получатель сообщения, а не собеседник.

    В событии от юзера там лежит id бота (277639678). Если он утечёт в
    Chat.id, бот начнёт отвечать сам себе и молча терять сообщения.
    """
    raw = FIXTURES["message_created"]
    assert raw["message"]["recipient"]["user_id"] == 277639678

    update = converters.to_update(raw, 1)
    assert update is not None
    assert update.message is not None
    assert update.message.chat.id != 277639678


def test_live_chat_id_falls_back_to_sender_not_recipient() -> None:
    """Если MAX однажды не пришлёт chat_id — берём отправителя-человека."""
    raw = json.loads(json.dumps(FIXTURES["message_created"]))
    del raw["message"]["recipient"]["chat_id"]

    update = converters.to_update(raw, 1)
    assert update is not None
    assert update.message is not None
    assert update.message.chat.id == 212843158


def test_live_message_callback() -> None:
    update = converters.to_update(FIXTURES["message_callback"], 2)

    assert update is not None
    callback = update.callback_query
    assert callback is not None
    assert callback.data == "accept:1"
    assert callback.from_user.id == 212843158
    # Сообщение с кнопкой приходит вместе с callback — chat берём из него.
    assert callback.message is not None
    assert callback.message.chat.id == 472107880


def test_live_keyboard_round_trip() -> None:
    """Клавиатура вернулась от MAX ровно такой, какой мы её отправили."""
    attachments = FIXTURES["message_callback"]["message"]["body"]["attachments"]
    keyboard = next(a for a in attachments if a["type"] == "inline_keyboard")

    assert keyboard["payload"]["buttons"] == [
        [
            {"payload": "accept:1", "text": "✅ Принять", "type": "callback"},
            {"payload": "edit:1", "text": "✏️ Доработать", "type": "callback"},
        ],
        [{"url": "https://max.ru", "text": "Ссылка", "type": "link"}],
    ]


def test_live_seq_exceeds_telegram_message_id_range() -> None:
    """seq у MAX — не маленький счётчик, а число порядка 1e17.

    В int64 влезает и aiogram переваривает, но узкое поле БД (integer вместо
    bigint) на этом сломается. Фиксируем как известное свойство платформы.
    """
    update = converters.to_update(FIXTURES["message_created"], 1)

    assert update is not None
    assert update.message is not None
    assert update.message.message_id > 2**31


def test_message_created_without_body_does_not_crash() -> None:
    """MAX умеет прислать message_created без самого сообщения.

    Авария на проде 2026-08-09: такое событие легло в очередь, converters
    падал на raw["message"], get_updates валился на каждом круге, и опрос
    MAX встал целиком — люди перестали получать ответы. Пропускаем событие
    так же, как любой неизвестный тип: один странный апдейт не должен
    останавливать бота.
    """
    raw = {
        "update_type": "message_created",
        "timestamp": 1754747657000,
        "user_locale": "ru",
    }

    assert converters.to_update(raw, 1) is None


def test_live_forward_keeps_text_and_author() -> None:
    """Пересланное сообщение: текст лежит внутри link, снаружи пусто.

    Так MAX и присылает форвард: ``body.text`` пустая строка, сам текст —
    в ``link.message.text``. Пока мы читали только внешний текст, событие
    выглядело сообщением ни о чём: у бота на другой стороне не было ни
    текста, ни признака пересылки, и человек не получал ответа вовсе.
    """
    update = converters.to_update(FIXTURES["message_forwarded"], 1)

    assert update is not None
    message = update.message
    assert message is not None
    assert message.text == "Не планировала, если это не обязательно"

    origin = message.forward_origin
    assert isinstance(origin, MessageOriginUser)
    assert origin.sender_user is not None
    assert origin.sender_user.first_name == "Мария"
    assert origin.sender_user.last_name == "Иванова"


def test_live_reply_keeps_quoted_message() -> None:
    """Ответ на сообщение: снаружи свой текст, внутри — тот, на который отвечают."""
    update = converters.to_update(FIXTURES["message_reply"], 1)

    assert update is not None
    message = update.message
    assert message is not None
    assert message.text == "Ответ на сообщение"

    quoted = message.reply_to_message
    assert quoted is not None
    assert quoted.text == "Вот ваш пост! Хотите внести какие-то правки?"
    assert quoted.from_user is not None
    assert quoted.from_user.is_bot is True
    assert message.forward_origin is None, "ответ — не пересылка"


def test_live_message_without_link_is_untouched() -> None:
    """Обычное сообщение остаётся прежним: ни пересылки, ни цитаты."""
    update = converters.to_update(FIXTURES["message_created"], 1)

    assert update is not None
    assert update.message is not None
    assert update.message.forward_origin is None
    assert update.message.reply_to_message is None
