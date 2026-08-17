"""Модели событий MAX. Весь входящий разбор идёт через них.

Правило, которое здесь важнее удобства: **модель обязана принимать всё, что
площадка прислала**. Каждое поле необязательное, незнакомые поля разрешены,
типы описывают то, что видели живьём, а не то, что обещает документация —
её на часть объектов попросту нет (страница ``LinkedMessage`` отдаёт 404).

Строгая модель на границе с площадкой опаснее отсутствия модели. Три из
четырёх наших аварий начинались одинаково: MAX прислал не то, чего ждали —
``message_created`` без тела, групповое сообщение без отправителя,
пересланное сообщение с пустым внешним текстом. Отказ разбора на таком
событии роняет пачку ``get_updates`` целиком: позиция не двигается, то же
событие приходит следующим кругом, и опрос встаёт навсегда. Поэтому разбор
одного события изолирован в ``converters.to_update`` и падение отдельного
события стоит ровно этого события.

``extra="allow"`` не только про устойчивость. Незнакомые поля видны через
``model_extra``, и тест на снятых с прода событиях сообщает, что MAX начал
присылать что-то новое. Поле ``link`` жило в событиях неизвестно сколько —
нашли его только тогда, когда понадобилась пересылка.
"""

from pydantic import BaseModel, ConfigDict, Field


class MaxModel(BaseModel):
    """Общее основание: незнакомое поле — не ошибка, а сведения."""

    model_config = ConfigDict(extra="allow")


class MaxUser(MaxModel):
    """Человек или бот в событиях MAX."""

    user_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    # Полное имя одной строкой: MAX присылает его вместе с частями.
    name: str | None = None
    is_bot: bool = False
    last_activity_time: int | None = None


class MaxRecipient(MaxModel):
    """Получатель сообщения.

    ``user_id`` тут — получатель конкретного сообщения, а не собеседник: в
    событии от человека боту здесь лежит id бота. Подстановка его в чат
    заставила бы бота отвечать самому себе, поэтому имя поля обманчиво, а
    сам разбор берёт ``chat_id``.
    """

    user_id: int | None = None
    chat_id: int | None = None
    chat_type: str | None = None


class MaxAttachmentPayload(MaxModel):
    """Полезная нагрузка вложения: у файлов ссылка, у клавиатуры кнопки."""

    url: str | None = None
    token: str | None = None
    buttons: list[list[dict[str, object]]] = Field(default_factory=list)


class MaxAttachment(MaxModel):
    """Вложение: изображение, файл, звук, видео или клавиатура."""

    type: str | None = None
    payload: MaxAttachmentPayload | None = None
    filename: str | None = None
    size: int | None = None


class MaxBody(MaxModel):
    """Тело сообщения. Тем же объектом MAX описывает и связанное сообщение."""

    # Идентификатор MAX — строка; целочисленный aiogram-овский собирается из seq.
    mid: str | None = None
    seq: int | None = None
    text: str | None = None
    attachments: list[MaxAttachment] = Field(default_factory=list)


class MaxLink(MaxModel):
    """Связь с другим сообщением: пересылка или ответ.

    Различаются по ``type``: ``forward`` и ``reply``. У пересланного внешний
    текст пустой, настоящий лежит здесь, в ``message.text``. ``chat_id`` у
    пересылки нулевой — исходный чат MAX не раскрывает.
    """

    type: str | None = None
    sender: MaxUser | None = None
    chat_id: int | None = None
    message: MaxBody | None = None


class MaxMessage(MaxModel):
    """Сообщение MAX."""

    sender: MaxUser | None = None
    recipient: MaxRecipient | None = None
    timestamp: int | None = None
    body: MaxBody | None = None
    link: MaxLink | None = None
    url: str | None = None


class MaxCallback(MaxModel):
    """Нажатие на кнопку."""

    callback_id: str | None = None
    payload: str | None = None
    user: MaxUser | None = None
    timestamp: int | None = None


class MaxUpdate(MaxModel):
    """Событие MAX целиком.

    Поля разных видов событий лежат рядом: ``message`` у сообщения,
    ``callback`` у нажатия, ``user`` и ``chat_id`` у «Начать». Обязательных
    среди них нет — вид события выясняется по ``update_type``.
    """

    update_type: str | None = None
    timestamp: int | None = None
    message: MaxMessage | None = None
    callback: MaxCallback | None = None
    user: MaxUser | None = None
    chat_id: int | None = None
    user_locale: str | None = None
