"""Реестр соответствия message_id ↔ mid.

Соответствие нужно, чтобы правка, удаление, закрепление и пересылка вообще
работали: aiogram оперирует целым message_id, MAX — строковым mid.
"""

from aiogram_max.mids import MidRegistry


def test_remembers_and_returns() -> None:
    registry = MidRegistry()
    registry.remember(11, "mid-abc")

    assert registry.get(11) == "mid-abc"


def test_unknown_seq_gives_none() -> None:
    assert MidRegistry().get(42) is None


def test_registry_does_not_grow_forever() -> None:
    """Без предела бот, живущий неделями, копил бы запись на сообщение.

    Это медленная утечка: сама по себе она никогда не проявится в тестах,
    зато проявится в проде через месяц аптайма.
    """
    registry = MidRegistry(capacity=3)

    for seq in range(10):
        registry.remember(seq, f"mid-{seq}")

    assert len(registry) == 3
    assert registry.get(9) == "mid-9"
    assert registry.get(0) is None


def test_recently_used_survives_eviction() -> None:
    """Вымываем давно нетронутое, а не просто давно записанное.

    Бот может править одно и то же сообщение долго — например, счётчик в
    закреплённом. Такая запись должна пережить поток новых.
    """
    registry = MidRegistry(capacity=3)
    registry.remember(1, "mid-1")
    registry.remember(2, "mid-2")
    registry.remember(3, "mid-3")

    registry.get(1)  # обратились — значит ещё нужна
    registry.remember(4, "mid-4")

    assert registry.get(1) == "mid-1"
    assert registry.get(2) is None
