"""Соответствие message_id (aiogram) ↔ mid (MAX).

aiogram знает сообщение по целому ``message_id``, MAX правит, удаляет,
закрепляет и пересылает по строковому ``mid``. Мостом служит ``seq`` —
целое поле MAX, которое мы и отдаём aiogram как ``message_id``.

Соответствие живёт в памяти процесса, поэтому правка сообщения переживает
рестарт только в пределах его жизни. Это ограничение платформы: обратного
преобразования mid → seq MAX не предоставляет.

Хранилище ограничено по размеру. Без предела бот, живущий неделями, копил
бы запись на каждое отправленное и принятое сообщение — медленная утечка,
которая проявляется в самый неудобный момент. Вымываем самые старые:
править сообщение месячной давности всё равно никто не станет.
"""

from __future__ import annotations

from collections import OrderedDict

DEFAULT_CAPACITY = 10_000


class MidRegistry:
    """Ограниченный по размеру словарь seq → mid, вымывающий старое."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._items: OrderedDict[int, str] = OrderedDict()
        self._capacity = capacity

    def remember(self, seq: int, mid: str) -> None:
        self._items[seq] = mid
        self._items.move_to_end(seq)
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def get(self, seq: int) -> str | None:
        mid = self._items.get(seq)
        if mid is not None:
            self._items.move_to_end(seq)
        return mid

    def __len__(self) -> int:
        return len(self._items)
