"""Модели событий MAX: принимают всё, ошибку изолируют.

Строгость на границе с площадкой опаснее её отсутствия: отказ разбора
роняет пачку get_updates, позиция не двигается, и опрос встаёт навсегда.
Эти тесты держат оба свойства — терпимость к незнакомому и изоляцию сбоя.
"""

import json
import pathlib

import pytest
from pydantic import BaseModel

from aiogram_max import converters
from aiogram_max.schemas import MaxUpdate

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "live_max_updates.json").read_text(
        encoding="utf-8"
    )
)


def _unknown_fields(model: BaseModel, path: str = "update") -> list[str]:
    """Поля, которых модель не знает, вместе с их путём."""
    found = [f"{path}.{name}" for name in (model.model_extra or {})]
    for name, value in model:
        if isinstance(value, BaseModel):
            found += _unknown_fields(value, f"{path}.{name}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, BaseModel):
                    found += _unknown_fields(item, f"{path}.{name}[{i}]")
    return found


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_live_events_have_no_unknown_fields(name: str) -> None:
    """Сторож: MAX начал присылать что-то новое — узнаём здесь.

    Поле link жило в событиях неизвестно сколько, и нашли мы его только
    когда понадобилась пересылка. Этот тест — способ узнавать о таком в
    день, когда событие снято, а не через месяцы.
    """
    event = MaxUpdate.model_validate(FIXTURES[name])

    assert _unknown_fields(event) == []


def test_unknown_field_is_accepted_not_rejected() -> None:
    """Незнакомое поле не ошибка: площадка не обязана согласовывать с нами."""
    raw = json.loads(json.dumps(FIXTURES["message_created"]))
    raw["message"]["body"]["новое_поле_max"] = {"что_то": 1}

    update = converters.to_update(raw, 1)

    assert update is not None
    assert update.message is not None
    assert update.message.text == "Тест"


def test_broken_event_is_skipped_without_touching_the_batch() -> None:
    """Событие с мусором в поле пропускается, а не роняет разбор.

    Падение здесь стоило бы всей пачки get_updates: позиция не сдвинулась
    бы, та же пачка пришла бы следующим кругом и упала снова.
    """
    raw = json.loads(json.dumps(FIXTURES["message_created"]))
    raw["message"]["recipient"]["chat_id"] = {"вместо": "числа"}

    assert converters.to_update(raw, 1) is None


def test_callback_without_id_is_skipped() -> None:
    """Нажатие без идентификатора роутить некуда — пропускаем молча."""
    raw = json.loads(json.dumps(FIXTURES["message_callback"]))
    del raw["callback"]["callback_id"]

    assert converters.to_update(raw, 1) is None


def test_message_without_body_still_parses() -> None:
    """Сообщение без тела — это пустой текст, а не отказ разбора."""
    raw = json.loads(json.dumps(FIXTURES["message_created"]))
    del raw["message"]["body"]

    update = converters.to_update(raw, 1)

    assert update is not None
    assert update.message is not None
    assert update.message.text is None
