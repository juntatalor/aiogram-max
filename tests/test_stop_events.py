"""bot_stopped и dialog_removed: позиция двигается, событие не теряется молча."""


from aiogram_max.converters import to_update


def test_bot_stopped_becomes_unknown_update() -> None:
    """bot_stopped конвертируется в Update без payloads — потребитель сам
    решает, что с ним делать, но позицию двигает штатно, не «прыжком»."""
    raw = {
        "chat_id": 123,
        "user": {"user_id": 42, "first_name": "Т"},
        "timestamp": 1794653614628,
        "update_type": "bot_stopped",
    }
    upd = to_update(raw, 100)
    assert upd is not None
    assert upd.update_id == 100
    assert upd.message is None
    assert upd.callback_query is None


def test_dialog_removed_becomes_unknown_update() -> None:
    """dialog_removed — то же: не сообщение и не кнопка, но и не пустота."""
    raw = {
        "chat_id": 123,
        "user": {"user_id": 42},
        "timestamp": 1794653614616,
        "update_type": "dialog_removed",
    }
    upd = to_update(raw, 101)
    assert upd is not None
    assert upd.update_id == 101


def test_unknown_type_still_none() -> None:
    """По-настоящему незнакомый тип остаётся None — молчать о новом событии
    подряд (без словаря известных «тихих» типов) библиотека не вправе."""
    assert to_update({"update_type": "something_new"}, 102) is None
