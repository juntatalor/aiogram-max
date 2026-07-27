"""Собирает сырые события MAX в файл — эталон для тестов и конвертеров.

Запуск:
    python scripts/capture_updates.py [секунд]

Пишет каждое событие как строку JSON в captured_updates.jsonl. Токен читает
из .env, в вывод не печатает.
"""

import asyncio
import json
import os
import pathlib
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "captured_updates.jsonl"


def load_token() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "MAX_BOT_TOKEN":
            return value.strip()
    return os.environ["MAX_BOT_TOKEN"]


async def main(seconds: int) -> None:
    token = load_token()
    deadline = time.monotonic() + seconds
    marker: int | None = None
    seen = 0

    async with httpx.AsyncClient(timeout=40) as client:
        with OUT.open("a", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                params: dict[str, object] = {"timeout": 20, "limit": 50}
                if marker is not None:
                    params["marker"] = marker
                response = await client.get(
                    "https://platform-api.max.ru/updates",
                    params=params,
                    headers={"Authorization": token},
                )
                response.raise_for_status()
                data = response.json()
                marker = data.get("marker", marker)
                for update in data.get("updates", []):
                    fh.write(json.dumps(update, ensure_ascii=False) + "\n")
                    fh.flush()
                    seen += 1
                    print(f"[{seen}] {update.get('update_type')}", flush=True)

    print(f"готово: {seen} событий → {OUT}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 120))
