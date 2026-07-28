"""Контракт, который миксины ожидают от сессии.

Методы разложены по областям (media, chats, settings), но все они опираются
на общее ядро: HTTP-вызов, политику расхождений, перевод разметки, реестр
mid. Раньше это было неявным знанием «мы всё равно внутри MaxSession» —
такое держится ровно до первой ошибки в имени.

Здесь ядро описано явно. Реализации нет намеренно: единственный наследник,
``MaxSession``, определяет все эти методы сам, а класс существует, чтобы
проверка типов ловила расхождения на месте, а не в рантайме.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx
    from aiogram import Bot
    from aiogram.client.default import Default
    from aiogram.types import MessageEntity

    from aiogram_max.mids import MidRegistry


class SessionCore:
    """Ядро сессии в терминах миксинов."""

    _client: httpx.AsyncClient
    _mids: MidRegistry

    async def _request(
        self,
        http_method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _reject(self, method_name: str) -> None:
        raise NotImplementedError

    def _degrade(self, what: str, detail: str) -> None:
        raise NotImplementedError

    def _render_markup(
        self,
        bot: Bot,
        text: str | None,
        parse_mode: str | Default | None,
        entities: list[MessageEntity] | None,
    ) -> tuple[str, str | None]:
        raise NotImplementedError

    def _require_mid(self, message_id: int | None) -> str:
        raise NotImplementedError

    async def _post_message(
        self, chat_id: Any, payload: dict[str, Any], *, wait_attachment: bool = False
    ) -> Any:
        raise NotImplementedError

    async def _send_when_attachment_ready(
        self, chat_id: Any, payload: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def _upload_attachment(
        self, bot: Bot, upload_type: str, file: Any
    ) -> dict[str, Any]:
        raise NotImplementedError
