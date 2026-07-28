# Покрытие методов Telegram Bot API

Что из aiogram уже работает поверх MAX, что можно сделать и что невозможно в принципе.

В aiogram **185 методов**. Большая часть из них описывает возможности, которых в MAX
нет как класса продукта, поэтому таблица ниже устроена по принципу «сначала то, что
имеет смысл, потом отсечённые семейства целиком».

Статусы:

| Статус | Что значит | Поведение библиотеки |
| --- | --- | --- |
| ✅ | Реализовано и проверено | работает |
| 🟡 | Аналог в MAX есть, руки не дошли | `NotImplementedYet` с подсказкой, куда смотреть. Сейчас таких нет |
| ⛔ | Аналога в MAX нет | `UnsupportedByMax` в строгом режиме, предупреждение в мягком |

Разница между 🟡 и ⛔ принципиальная: первое чинится патчем, второе — свойство
платформы. Поэтому и исключения разные.

## Реализовано

| Метод aiogram | Эндпоинт MAX | Примечание |
| --- | --- | --- |
| `GetUpdates` | `GET /updates` | `offset` ↔ `marker`, `update_id` — позиция в ленте |
| `SendMessage` | `POST /messages` | текст, inline-клавиатура, разметка, `reply_to`, `notify` |
| `EditMessageText` | `PUT /messages` | `message_id` (int) сшивается с `mid` (str) через `seq` |
| `DeleteMessage` | `DELETE /messages` | там же |
| `AnswerCallbackQuery` | `POST /answers` | пустой ответ — no-op, у MAX нет «просто снять часики» |
| `GetFile` | — | у MAX ссылка приходит вместе с сообщением, отдаём её как `file_path` |
| `GetMe` | `GET /me` | |
| `SendChatAction` | `POST /chats/{id}/actions` | индикатор есть и в личке, и в группе; названия действий переводятся |
| `SendPhoto` | `POST /uploads` → attachment | подпись, разметка, клавиатура; ссылка вместо файла — без загрузки |
| `SendDocument` | то же, `type=file` | |
| `SendVideo`, `SendAnimation` | то же, `type=video` | |
| `SendAudio`, `SendVoice` | то же, `type=audio` | |
| `EditMessageReplyMarkup` | `PUT /messages` | пустой markup убирает кнопки |
| `GetChat` | `GET /chats/{id}` | тип, название, ссылка-приглашение |
| `GetChatMemberCount` | там же | отдельной ручки нет, берём поле чата |
| `GetChatMember` | `/members/me` либо `/members` | выборки по user_id у MAX нет |
| `GetChatAdministrators` | `/members/admins` | доступно только администратору чата |
| `PromoteChatMember` | `POST /members/admins` | права целиком, по отдельности MAX их не знает |
| `PinChatMessage` | `PUT /chats/{id}/pin` | |
| `UnpinChatMessage`, `UnpinAllChatMessages` | `DELETE /chats/{id}/pin` | закреплённое одно, вызов общий |
| `SetChatTitle` | `PATCH /chats/{id}` | |
| `LeaveChat` | `DELETE /members/me` | |
| `BanChatMember` | `DELETE /members` | удаление, не бан: юзер вернётся по ссылке. Владельца не удаляет, отвечая успехом |
| `UnbanChatMember` | — | бана нет, снимать нечего |
| `SetWebhook` | `POST /subscriptions` | прежняя подписка снимается: у MAX это список, а не один адрес |
| `DeleteWebhook` | `DELETE /subscriptions` | снимает все подписки |
| `GetWebhookInfo` | `GET /subscriptions` | отдаём первую: у Telegram адрес один |
| `SetMyCommands`, `GetMyCommands`, `DeleteMyCommands` | `PATCH /me`, `GET /me` | один список на бота, без scope и языков |
| `SendLocation` | attachment `location` | координаты вне `payload`, иначе «latitude cannot be null» |
| `SendMediaGroup` | несколько attachments | альбома как сущности нет; подпись первого элемента становится текстом |
| `ForwardMessage` | `link: {type: forward}` | ссылка на исходное, а не копия |
| `EditMessageCaption` | `PUT /messages` | подпись у MAX — это текст сообщения |
| `SendSticker` | attachment `sticker` | нужен код стикера MAX, телеграмный file_id не подходит |
| `EditMessageMedia` | `PUT /messages` | новый файл заливается заново |
| `SetChatPhoto` | `PATCH /chats/{id}` | иконка ставится токеном; ссылку MAX не принимает |

## Ближайший план

Порядок выбран по тому, как часто это нужно живому боту, а не по алфавиту.

### Осталось

| Метод | Эндпоинт MAX |
| --- | --- |
| 🟡 `SetChatPhoto` | `PATCH /chats/{id}` с загруженной иконкой |

| Метод | Комментарий |
| --- | --- |
| 🟡 `SendMediaGroup` | несколько attachments в одном `POST /messages` |
| 🟡 `SendLocation` | attachment типа `location` |
| 🟡 `ForwardMessage` | у сообщений MAX есть `link`, тип `forward` — нужно проверить на живом API |
| 🟡 `EditMessageCaption`, `EditMessageMedia` | `PUT /messages` с вложениями |
| 🟡 `SendSticker` | attachment типа `sticker`, но стикер-паки MAX живут своей жизнью |

## Чего в MAX нет

Отсечено целыми семействами — это не «руки не дошли», а отсутствующая
функциональность платформы. Вызов такого метода даёт `UnsupportedByMax`.

| Семейство | Методов | Примеры |
| --- | --- | --- |
| Стикеры и кастомные эмодзи (управление) | 20 | `CreateNewStickerSet`, `SetStickerEmojiList` |
| Платежи, звёзды, подарки | 21 | `SendInvoice`, `RefundStarPayment`, `SendGift` |
| Бизнес-аккаунты | 12 | `GetBusinessConnection`, `SetBusinessAccountBio` |
| Форумы и топики | 12 | `CreateForumTopic`, `CloseGeneralForumTopic` |
| Эфемерные сообщения, черновики, чек-листы | 12 | `SendChecklist`, `EditEphemeralMessageText` |
| Приглашения и заявки на вступление | 11 | `CreateChatInviteLink`, `ApproveChatJoinRequest` |
| Inline-режим | 5 | `AnswerInlineQuery`, `AnswerWebAppQuery` |
| Истории | 4 | `PostStory`, `EditStory` |
| Верификация | 4 | `VerifyChat`, `VerifyUser` |
| Игры | 3 | `SendGame`, `SetGameScore` |
| Реакции на сообщения | 3 | `SetMessageReaction` |
| Telegram Passport | 1 | `SetPassportDataErrors` |

Плюс поштучно: `SendPoll`, `StopPoll`, `SendDice`, `SendVenue`, `SendContact`,
`EditMessageLiveLocation`, `StopMessageLiveLocation`, `SetChatPermissions`,
`RestrictChatMember`, `SetChatMenuButton`, `GetChatMenuButton`,
`GetUserProfilePhotos`, `GetUserChatBoosts`, `LogOut`, `Close`,
`SetMyDefaultAdministratorRights`, `SetChatMemberTag`, `SetChatDescription`.

Про `SetChatDescription` отдельно: эндпоинт вроде бы есть, но описание не
меняется. `PATCH /chats/{id}` с этим полем отвечает `200` и отдаёт чат, где
описание прежнее; если послать одно описание без названия — приходит ошибка
в теле при статусе `200`. Раз изменение не применяется, честнее считать
метод неподдерживаемым, чем возвращать успех.

## Чего нет в Telegram, но есть у MAX

Обратные расхождения тоже бывают, и через aiogram их не вызвать:

* `POST /chats/{id}/members` — добавить участника в чат. У Telegram такого
  метода для ботов нет вовсе, поэтому и отображать некуда.
* `GET /chats` — список всех чатов бота. Ближайший телеграмный аналог —
  вести собственный учёт по событиям.

## Как устроена загрузка вложений

Схема двухшаговая, и в справочнике описана наполовину — ниже то, что
выяснено на живом API:

1. `POST /uploads?type=image|video|audio|file` отдаёт `{"url": ...}`.
   Токена на этом шаге нет, хотя документация обещает его сразу.
2. На полученный url кладётся файл, `multipart`, имя поля — **`data`**.
   Ответ различается: у картинок `{"photos": {"<ключ>": {"token": ...}}}`,
   у файлов, видео и аудио — `{"fileId": ..., "token": ...}`.
3. Токен уходит в сообщение как `{"type": ..., "payload": {"token": ...}}`.

Две вещи, на которых легко споткнуться:

* **Заливка отвечает `200` даже на отказ** — ошибка лежит в теле
  (`{"error_code": "503", "error_data": "IMAGE_INVALID_FORMAT"}`).
* **Имя файла важнее содержимого.** Хранилище сверяет расширение и
  content-type: `.wav` с `audio/wav` отвергается как `415`, хотя wav заявлен
  в документации; тот же файл под именем `.mp3` принимается. Библиотека
  подставляет content-type по имени файла.
* **У анимации нет своего хранилища.** Телеграмная анимация — gif либо
  беззвучный mp4; gif принимает картиночное хранилище, видео-хранилище
  отвечает на него `415`. Тип выбирается по расширению.
* **Поле multipart разное:** картинки и файлы ждут `data`, аудио и видео —
  `file`. Чужое имя тоже даёт `415`.
* **Файл не готов сразу.** Отправка сообщения сразу после заливки отвечает
  `400 attachment.not.ready`: MAX ещё обрабатывает файл. Библиотека ждёт и
  повторяет с нарастающей паузой — в Telegram такого рукопожатия нет, и
  портированный бот о нём не подозревает.

## Разметка — отдельная история

Форматирование не метод, а параметр, и работает уже сейчас: `parse_mode`
(`HTML`, `MarkdownV2`, `Markdown`) и `entities` переводятся в html, понятный MAX.
Подробности и таблица поддерживаемых тегов — в [README](../README.md#разметка).

`caption` и `caption_entities` у вложений — тоже покрыты.

## Как добавить метод

1. Найти эндпоинт в [dev.max.ru](https://dev.max.ru/docs-api).
2. Написать `_handler` в `MaxSession` и зарегистрировать в `_HANDLERS`.
3. Убрать метод из `NOT_IMPLEMENTED_PR_WELCOME`.
4. Тест с `FakeMax`: проверять тело запроса, а не только «не упало».
5. Если параметру аналога нет — `_degrade`, а не молчаливый пропуск.

Подробнее — в [CONTRIBUTING.md](../CONTRIBUTING.md).
