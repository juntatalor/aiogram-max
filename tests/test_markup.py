"""Перевод телеграмной разметки в html, понятный MAX.

Ожидания сверены с живым MAX: он вернул разобранную разметку (типы strong,
emphasized, underline, strikethrough, monospaced, link), и таблица в README
составлена по этому ответу, а не по документации — официальная страница
«Форматирование» отдаёт 404.
"""

from aiogram.types import MessageEntity

from aiogram_max.markup import entities_to_html, markdown_to_html


def _collector() -> tuple[list[tuple[str, str]], object]:
    losses: list[tuple[str, str]] = []

    def degrade(what: str, detail: str) -> None:
        losses.append((what, detail))

    return losses, degrade


def _md(text: str) -> str:
    _, degrade = _collector()
    return markdown_to_html(text, degrade)  # type: ignore[arg-type]


def test_emphasis_maps_to_max_tags() -> None:
    """Жирный, курсив, подчёркнутый и зачёркнутый — каждый в свой тег.

    Подчёркивание здесь и есть причина целиться в html: телеграмное
    ``__текст__`` в CommonMark означает жирный, так что через markdown
    оно бы потерялось молча.
    """
    assert _md("*жирный*") == "<b>жирный</b>"
    assert _md("_курсив_") == "<i>курсив</i>"
    assert _md("__подчёркнутый__") == "<u>подчёркнутый</u>"
    assert _md("~зачёркнутый~") == "<s>зачёркнутый</s>"


def test_code_and_links() -> None:
    assert _md("`моно`") == "<code>моно</code>"
    assert _md("```\nблок\n```") == "<pre>блок\n</pre>"
    assert _md("[текст](https://max.ru)") == '<a href="https://max.ru">текст</a>'


def test_code_span_keeps_markup_characters_literal() -> None:
    """Внутри кода разметки нет: звёздочка остаётся звёздочкой."""
    assert _md("`a * b`") == "<code>a * b</code>"


def test_escaped_characters_lose_backslash() -> None:
    """Экранирование MarkdownV2 снимается, символ остаётся текстом."""
    assert _md(r"5\*5 и \_нижнее\_") == "5*5 и _нижнее_"


def test_html_special_chars_are_escaped() -> None:
    """Текст едет в html, поэтому < > & обязаны быть экранированы."""
    assert _md("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_unclosed_delimiter_does_not_break_message() -> None:
    """Незакрытый разделитель закрываем сами: оборванный тег испортил бы всё."""
    assert _md("*жирный без пары") == "<b>жирный без пары</b>"


def test_spoiler_is_degraded_but_text_survives() -> None:
    losses, degrade = _collector()
    out = markdown_to_html("||секрет||", degrade)  # type: ignore[arg-type]
    assert out == "секрет"
    assert [what for what, _ in losses] == ["spoiler", "spoiler"]


def test_quote_is_degraded_but_text_survives() -> None:
    losses, degrade = _collector()
    out = markdown_to_html("> цитата", degrade)  # type: ignore[arg-type]
    assert out == "цитата"
    assert losses and losses[0][0] == "blockquote"


def test_custom_emoji_keeps_label() -> None:
    losses, degrade = _collector()
    out = markdown_to_html("![😀](tg://emoji?id=5368324170671202286)", degrade)  # type: ignore[arg-type]
    assert out == "😀"
    assert losses and losses[0][0] == "custom_emoji"


def test_entities_render_without_parsing() -> None:
    """Вход через entities — смещения, а не грамматика: перевод точный."""
    _, degrade = _collector()
    text = "жирный и курсив"
    entities = [
        MessageEntity(type="bold", offset=0, length=6),
        MessageEntity(type="italic", offset=9, length=6),
    ]
    out = entities_to_html(text, entities, degrade)  # type: ignore[arg-type]
    assert out == "<b>жирный</b> и <i>курсив</i>"


def test_unsupported_entity_is_dropped_with_warning() -> None:
    """Спойлера у MAX нет — оформление снимаем, текст оставляем."""
    losses, degrade = _collector()
    out = entities_to_html(
        "тайна",
        [MessageEntity(type="spoiler", offset=0, length=5)],
        degrade,  # type: ignore[arg-type]
    )
    assert out == "тайна"
    assert losses and losses[0][0] == "entity spoiler"
