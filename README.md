# aiogram-max

Запускает бота, написанного на **aiogram**, в мессенджере **MAX** — без правок кода бота.

```python
from aiogram import Dispatcher
from aiogram_max import make_bot

bot = make_bot(max_token="...")  # единственная изменённая строка
await Dispatcher().start_polling(bot)
```

Роутеры, фильтры (`Command`, магический `F`), FSM, middleware и типы остаются
родными aiogram-овскими.

## Статус

**Прототип, проверенный на живом MAX.** Настоящий `Dispatcher.start_polling`
ходит в MAX Bot API, хендлеры срабатывают, ответы и `callback.answer()`
доезжают до мессенджера. Проверено на реальном боте: `/start`, обычный текст,
клик по inline-кнопке.

## Как это работает

Весь исходящий трафик aiogram проходит через одну функцию:

```
BaseSession.make_request(bot, method: TelegramMethod, timeout) -> TelegramType
```

`Bot` не ходит в сеть сам: он собирает типизированный объект метода
(`SendMessage`, `GetUpdates`, `AnswerCallbackQuery`, …) и отдаёт его сессии.
Мы подменяем сессию — и перехватываем поток целиком:

```
Бот на aiogram  (код не меняется)
  Dispatcher / Router / фильтры / FSM
  Bot.send_message(...)
        │  TelegramMethod
        ▼
  MaxSession(BaseSession)     ← вся библиотека здесь
        │  HTTP
        ▼
  MAX Bot API
```

Long polling тоже работает штатно: aiogram сам зовёт `GetUpdates`, а сессия
ходит в `GET /updates?marker=` и собирает из событий MAX валидные
aiogram-`Update`.

Отличие от [obabot](https://github.com/Korean-DOG/obabot), решающего похожую
задачу: там своя система типов (`obabot.Message`, `obabot.FSMContext`) и
импорты в боте меняются. Здесь типы остаются aiogram-овскими, подменяется
только транспорт.

## Что уже проверено тестами

| Что | Как проверено |
| --- | --- |
| MAX-событие → валидный aiogram `Update` | `test_get_updates_returns_aiogram_updates` |
| Роутер + фильтр `Command` | `test_dispatcher_routes_message_to_handler` |
| Inline-клавиатура → MAX `inline_keyboard` | там же, сверка тела запроса |
| Callback + магический фильтр `F.data` | `test_callback_query_flows_through_aiogram` |
| FSM (`StatesGroup`, `FSMContext`) | `test_fsm_state_survives_platform_swap` |
| Родной `Dispatcher.start_polling` | `test_native_polling_loop_delivers_max_events` |
| Правка сообщения (`seq` ↔ `mid`) | `test_edit_message_uses_max_mid` |
| Неподдерживаемый метод | `test_unsupported_method_raises_in_strict_mode` |
| Живые payload'ы MAX | `tests/test_live_fixtures.py` (6 тестов на снятых с API событиях) |
| Потеря кнопки без аналога | `test_dropped_button_warns_but_keeps_the_rest` |
| Маппинг parse_mode / notify / reply | `test_supported_params_are_mapped_not_dropped` |

## Неподдерживаемое в MAX

У MAX нет части возможностей Telegram. Политика задаётся при создании бота:

```python
make_bot(token)  # WARN, по умолчанию
make_bot(token, unsupported=UnsupportedPolicy.STRICT)
```

Расхождения бывают трёх видов:

1. **Метода нет вовсе** (`SendPoll`, `SendDice`). `WARN` — предупреждение и
   пропуск, `STRICT` — `UnsupportedByMax` с именем метода.
2. **Метод есть, а параметра нет** (кнопка `web_app`, `switch_inline_query`).
   Такая кнопка выбрасывается, остальные остаются: `WARN` пишет в лог что
   именно потерялось, `STRICT` падает. Молча не выбрасываем никогда —
   «кнопка исчезла, а бот не упал» ищется потом часами.
3. **Семантика другая** — это работа слоя конвертации, а не политики:
   `message_id` (int) ↔ MAX `mid` (str) сшиваются через `seq`.

Где аналог есть — параметр переводится, а не теряется:

| aiogram | MAX |
| --- | --- |
| `parse_mode="HTML"` | `format: html` |
| `parse_mode="MarkdownV2"` | `format: markdown` + предупреждение (у MAX CommonMark) |
| `disable_notification=True` | `notify: false` |
| `reply_to_message_id` | `link: {type: reply, mid}` |

Отдельный случай — `SendChatAction`: у MAX нет typing-индикатора, и это
тихий no-op даже в строгом режиме. Индикатор набора не меняет смысла диалога,
ронять из-за него бота незачем.

По той же причине no-op — пустой `callback.answer()`. В Telegram он снимает
индикатор загрузки на кнопке и стоит почти в каждом хендлере; у MAX такой
семантики нет, `POST /answers` с пустым телом отвечает `400 proto.payload`
(«`message` or `notification` required»). Запрос не уходит вовсе. С текстом —
`answer("Принято")` — уходит как `notification`.

## Грабли MAX, которые стоит знать

* **`recipient.user_id` — это получатель сообщения, а не собеседник.** В
  событии от пользователя там лежит id бота, в сообщении бота пользователю —
  id пользователя. Подставите его как `chat_id` — бот начнёт молча отвечать
  сам себе. `chat_id` в диалоге MAX присылает, брать надо только его.
* **Позиция в ленте — `marker`, и она же `update_id`.** MAX отдаёт в ответе
  «следующую ожидаемую» позицию, что совпадает по смыслу с телеграмным
  `offset`. Поэтому `update_id` события — его позиция в ленте (`marker - 1`
  для последнего события пачки), а не порядковый номер внутри процесса.
  Для бота с дедупом по `update_id` это принципиально: счётчик из памяти
  после рестарта начинается заново, и свежие события выглядят уже
  обработанными.
* **`seq` — не маленький счётчик.** Живое значение: `116993690454357274`.
  В `int64` влезает и aiogram переваривает, но узкое поле БД (`integer`
  вместо `bigint`) на этом сломается.
* **`/start` приходит обычным `message_created`**, а не отдельным событием —
  фильтр `Command` работает без спецобработки.
* **Идентификатор сообщения двойной**: aiogram знает `message_id` (int из
  `seq`), MAX правит и удаляет по строковому `mid`. Соответствие держит
  сессия в памяти, поэтому правка сообщения переживает рестарт только в
  пределах жизни процесса.

## Что ещё не сделано
* Вложения: `POST /uploads` для отправки, скачивание по прямой ссылке.
* Webhook (у MAX это рекомендованный для прода транспорт).
* Форматирование: Telegram HTML/MarkdownV2 против CommonMark у MAX.
* `bot.id` — заглушка `0`, настоящий id надо брать из `GET /me`.
* Покрыты 7 методов из ~100 в Telegram Bot API; остальное — по мере надобности.

## Установка

```bash
pip install aiogram-max      # после публикации; пока — pip install -e .
```

Python 3.12+, единственные зависимости — `aiogram` и `httpx`. Библиотека
типизирована (`py.typed`), mypy strict проходит без ignore'ов.

## Разработка

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest -q
```

Как добавить метод и чем `NotImplementedYet` отличается от `UnsupportedByMax` —
в [CONTRIBUTING.md](CONTRIBUTING.md).

## Лицензия

MIT
