"""Перевод разметки Telegram в разметку MAX.

Цель — html, а не CommonMark, и это не вкусовщина. Проверка на живом API
(таблица в README) показала: тип ``underline`` MAX отдаёт только за ``<u>``.
Через markdown подчёркивание недостижимо в принципе — телеграмное
``__текст__`` в CommonMark означает жирный, так что конверсия в markdown
молча превратила бы подчёркнутое в жирное. Плюс html не требует правил
экранирования, которых в MarkdownV2 полтора десятка, да ещё и разных внутри
кодовых спанов.

Входа два, и оба сходятся в html:

* ``entities`` — смещения, а не грамматика. Точный перевод, рендер берём
  готовый из aiogram.
* ``parse_mode=MarkdownV2`` — нужен разбор текста, этим занят ``_Scanner``.

Чего у MAX нет вовсе — спойлер, цитата, кастомные эмодзи — уходит в
``degrade``: текст сохраняется, оформление теряется, в лог пишется что
именно. Молча не теряем ничего.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum

from aiogram.types import MessageEntity
from aiogram.utils.text_decorations import html_decoration

Degrade = Callable[[str, str], None]


class MarkupPolicy(StrEnum):
    """Что делать с разметкой по дороге в MAX."""

    CONVERT = "convert"
    """Перевести в html, понятный MAX. По умолчанию."""

    RAW = "raw"
    """Отдать текст как есть, только проставить format. Для тех, кто
    форматирует сам и не хочет посредника."""


# Проверено на живом MAX (см. README): эти теги он превращает в разметку.
# blockquote и tg-spoiler не распознаются, поэтому соответствующие entity
# отбрасываем до рендера, а не после.
_SUPPORTED_ENTITIES = frozenset(
    {
        "bold",
        "italic",
        "underline",
        "strikethrough",
        "code",
        "pre",
        "text_link",
        "url",
        "text_mention",
        "mention",
    }
)

_ENTITY_LOSS = {
    "spoiler": "у MAX нет спойлера",
    "blockquote": "у MAX нет цитаты",
    "expandable_blockquote": "у MAX нет цитаты",
    "custom_emoji": "у MAX нет кастомных эмодзи",
}


def entities_to_html(
    text: str, entities: Iterable[MessageEntity] | None, degrade: Degrade
) -> str:
    """``text`` + ``entities`` → html для MAX.

    Неподдерживаемые entity выбрасываются до рендера: их текст остаётся,
    оформление теряется. Рендер — ``html_decoration`` из aiogram, там уже
    решён вопрос смещений в UTF-16.
    """
    kept: list[MessageEntity] = []
    for entity in entities or []:
        loss = _ENTITY_LOSS.get(entity.type)
        if loss is not None:
            degrade(f"entity {entity.type}", f"{loss}, отправлено без оформления")
            continue
        if entity.type in _SUPPORTED_ENTITIES:
            kept.append(entity)
        else:
            degrade(f"entity {entity.type}", "нет аналога в MAX")
    return html_decoration.unparse(text, kept)


def markdown_to_html(text: str, degrade: Degrade) -> str:
    """MarkdownV2 (и legacy Markdown) → html для MAX."""
    return _Scanner(text, degrade).run()


_ESCAPABLE = set("_*[]()~`>#+-=|{}.!\\")

# Двухсимвольные раньше односимвольных: иначе ``__`` съедается как два ``_``.
_DELIMITERS: tuple[tuple[str, str], ...] = (
    ("||", "spoiler"),
    ("__", "u"),
    ("*", "b"),
    ("_", "i"),
    ("~", "s"),
)


def _escape(chunk: str) -> str:
    return chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Scanner:
    """Однопроходный разбор MarkdownV2 с выдачей html.

    Промежуточных entity нет намеренно: они считаются в смещениях UTF-16, а
    здесь эта бухгалтерия не нужна ни для чего.

    Порядок важен: сначала снимается экранирование и вырезаются кодовые
    спаны (внутри них разметки нет), и только потом разбираются эмфазы.
    """

    def __init__(self, text: str, degrade: Degrade) -> None:
        self._text = text
        self._degrade = degrade
        self._out: list[str] = []
        self._open: list[str] = []
        self._pos = 0

    def run(self) -> str:
        text = self._text
        while self._pos < len(text):
            if self._take_escape():
                continue
            if self._take_pre():
                continue
            if self._take_code():
                continue
            if self._take_link():
                continue
            if self._take_delimiter():
                continue
            if self._take_quote_marker():
                continue
            self._out.append(_escape(text[self._pos]))
            self._pos += 1

        # Незакрытый разделитель — ошибка в исходном тексте: Telegram на нём
        # отвечает 400. Закрываем сами, потому что оборванный тег испортил бы
        # всё сообщение целиком.
        while self._open:
            self._out.append(f"</{self._open.pop()}>")
        return "".join(self._out)

    def _take_escape(self) -> bool:
        text = self._text
        if text[self._pos] != "\\" or self._pos + 1 >= len(text):
            return False
        nxt = text[self._pos + 1]
        if nxt not in _ESCAPABLE:
            return False
        self._out.append(_escape(nxt))
        self._pos += 2
        return True

    def _take_pre(self) -> bool:
        text = self._text
        if not text.startswith("```", self._pos):
            return False
        end = text.find("```", self._pos + 3)
        if end == -1:
            return False
        body = text[self._pos + 3 : end]
        # Первая строка после ``` — либо язык (в MAX ему места нет), либо
        # пусто. В обоих случаях в тело кода она не входит.
        if "\n" in body:
            first, rest = body.split("\n", 1)
            head = first.strip()
            if not head or " " not in head:
                body = rest
        self._out.append(f"<pre>{_escape(body)}</pre>")
        self._pos = end + 3
        return True

    def _take_code(self) -> bool:
        text = self._text
        if text[self._pos] != "`":
            return False
        end = text.find("`", self._pos + 1)
        if end == -1:
            return False
        body = text[self._pos + 1 : end].replace("\\`", "`").replace("\\\\", "\\")
        self._out.append(f"<code>{_escape(body)}</code>")
        self._pos = end + 1
        return True

    def _take_link(self) -> bool:
        text = self._text
        emoji = text.startswith("![", self._pos)
        start = self._pos + 2 if emoji else self._pos + 1
        if not emoji and text[self._pos] != "[":
            return False
        close = text.find("](", start)
        if close == -1:
            return False
        end = text.find(")", close + 2)
        if end == -1:
            return False
        label = text[start:close]
        url = text[close + 2 : end]
        self._pos = end + 1
        if emoji or url.startswith("tg://emoji"):
            self._degrade(
                "custom_emoji", "у MAX нет кастомных эмодзи, оставлен только текст"
            )
            self._out.append(_escape(label))
            return True
        self._out.append(f'<a href="{_escape(url)}">{_escape(label)}</a>')
        return True

    def _take_delimiter(self) -> bool:
        text = self._text
        for delim, tag in _DELIMITERS:
            if not text.startswith(delim, self._pos):
                continue
            self._pos += len(delim)
            if tag == "spoiler":
                # Открывающий и закрывающий || просто исчезают: текст между
                # ними остаётся видимым и неформатированным.
                self._degrade("spoiler", "у MAX нет спойлера, текст оставлен открытым")
                return True
            if self._open and self._open[-1] == tag:
                self._out.append(f"</{self._open.pop()}>")
            else:
                self._open.append(tag)
                self._out.append(f"<{tag}>")
            return True
        return False

    def _take_quote_marker(self) -> bool:
        text = self._text
        if text[self._pos] != ">":
            return False
        at_line_start = self._pos == 0 or text[self._pos - 1] == "\n"
        if not at_line_start:
            return False
        self._degrade("blockquote", "у MAX нет цитаты, отправлено обычным текстом")
        self._pos += 1
        if self._pos < len(text) and text[self._pos] == " ":
            self._pos += 1
        return True
