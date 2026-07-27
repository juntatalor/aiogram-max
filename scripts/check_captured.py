"""Прогоняет захваченные события MAX через наши конвертеры.

Показывает, во что превратился каждый сырой update и не потерялось ли что-то
по дороге. Сеть не трогает — работает по файлу captured_updates.jsonl.

    python scripts/check_captured.py
"""

import json
import pathlib

from aiogram_max import converters

ROOT = pathlib.Path(__file__).resolve().parent.parent
DUMP = ROOT / "captured_updates.jsonl"


def main() -> None:
    if not DUMP.exists() or not DUMP.stat().st_size:
        print("дамп пуст — событий пока не поймали")
        return

    for i, line in enumerate(DUMP.read_text(encoding="utf-8").splitlines(), start=1):
        raw = json.loads(line)
        kind = raw.get("update_type")
        print(f"\n[{i}] update_type={kind}")
        print("    сырое:", json.dumps(raw, ensure_ascii=False)[:400])

        update = converters.to_update(raw, i)
        if update is None:
            print("    → конвертер вернул None (тип события не обрабатываем)")
            continue

        if update.message:
            msg = update.message
            print(
                f"    → Message id={msg.message_id} chat={msg.chat.id}/{msg.chat.type} "
                f"from={msg.from_user.id if msg.from_user else None} text={msg.text!r}"
            )
            if msg.document:
                print(
                    f"       document name={msg.document.file_name} "
                    f"size={msg.document.file_size} url={msg.document.file_id[:80]}"
                )
            if msg.photo:
                print(f"       photo url={msg.photo[0].file_id[:80]}")
        if update.callback_query:
            cb = update.callback_query
            print(
                f"    → CallbackQuery id={cb.id} data={cb.data!r} "
                f"from={cb.from_user.id} message={'есть' if cb.message else 'нет'}"
            )


if __name__ == "__main__":
    main()
