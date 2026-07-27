# aiogram-max

Запускает бота, написанного на **aiogram**, в мессенджере **MAX** — без правок кода бота.

```python
from aiogram import Dispatcher
from aiogram_max import make_bot

bot = make_bot(max_token="...")      # единственная изменённая строка
await Dispatcher().start_polling(bot)
```

Роутеры, фильтры (`Command`, магический `F`), FSM, middleware и типы остаются
родными aiogram-овскими.

## Статус

**Прототип.** Гипотеза проверена автотестами против фейкового MAX; на живом
токене MAX ещё не гонялось.

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

## Неподдерживаемое в MAX

У MAX нет части возможностей Telegram. Политика задаётся при создании бота:

```python
make_bot(token, unsupported=UnsupportedPolicy.STRICT)   # по умолчанию
make_bot(token, unsupported=UnsupportedPolicy.LENIENT)
```

* `STRICT` — `UnsupportedByMax` с именем метода. Расхождение видно сразу.
* `LENIENT` — предупреждение в лог, вызов пропускается.

Отдельный случай — `SendChatAction`: у MAX нет typing-индикатора, и это
тихий no-op даже в строгом режиме. Индикатор набора не меняет смысла диалога,
ронять из-за него бота незачем.

## Что ещё не сделано

* Живой прогон на реальном токене MAX.
* Вложения: `POST /uploads` для отправки, скачивание по прямой ссылке.
* Webhook (у MAX это рекомендованный для прода транспорт).
* Форматирование: Telegram HTML/MarkdownV2 против CommonMark у MAX.
* `bot.id` — заглушка `0`, настоящий id надо брать из `GET /me`.
* Покрыты 7 методов из ~100 в Telegram Bot API; остальное — по мере надобности.

## Разработка

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```
