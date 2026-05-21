# FreelanceHunt → Telegram (Разработка ботов)

Telegram-бот в Docker. Раз в минуту через **официальное API FreelanceHunt**
(`api.freelancehunt.com/v2/projects`) берёт открытые проекты в выбранной
категории (по умолчанию — «Разработка ботов», `skill_id=180`) и присылает
уведомления о новых: заголовок, ссылка на проект, бюджет, краткое описание,
время публикации и ссылка на категорию.

> API выбран вместо парсинга HTML: страница категории закрыта Cloudflare
> WAF, который режет запросы с дата-центровых IP. API стабильно работает
> по Bearer-токену.

## Запуск

1. Создай бота у [@BotFather](https://t.me/BotFather), получи `TELEGRAM_BOT_TOKEN`.
2. Узнай свой `TELEGRAM_CHAT_ID` (например, через [@userinfobot](https://t.me/userinfobot)).
   Напиши боту `/start` — иначе Telegram не даст слать тебе сообщения.
3. Получи `FREELANCEHUNT_TOKEN`: <https://freelancehunt.com/my/api> →
   *Personal Access Token*. Это бесплатно и занимает минуту.
4. Скопируй пример конфига и заполни значения:

   ```sh
   cp .env.example .env
   # отредактируй .env
   ```

5. Запусти:

   ```sh
   docker compose up -d --build
   ```

6. Логи:

   ```sh
   docker compose logs -f
   ```

## Настройки (`.env`)

| Переменная                   | По умолчанию | Описание                                                                                              |
| ---------------------------- | ------------ | ----------------------------------------------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`         | —                 | Токен бота от BotFather                                                                          |
| `TELEGRAM_CHAT_ID`           | —                 | ID чата получателя                                                                               |
| `FREELANCEHUNT_TOKEN`        | —                 | Personal Access Token FreelanceHunt API                                                          |
| `SKILL_ID`                   | `180`             | ID категории FreelanceHunt (180 = «Разработка ботов»)                                            |
| `POLL_INTERVAL`              | `60`              | Период опроса API в секундах                                                                     |
| `SEND_EXISTING_ON_FIRST_RUN` | `false`           | При первом запуске прислать все имеющиеся проекты. По умолчанию они помечаются как уже виденные  |
| `PAGE_SIZE`                  | `5`               | Размер страницы в списке `/start`                                                                |
| `HISTORY_SIZE`               | `50`              | Сколько последних проектов хранить для пагинации                                                 |
| `LISTING_URL`                | (категория ботов) | Веб-URL категории — используется только как ссылка в подвале уведомления                         |
| `CATEGORY_NAME`              | `Разработка ботов`| Название категории для вывода                                                                    |

## Команды бота

- `/start` — приветствие и кнопка **«📋 Последние проекты»**. По клику
  открывается список последних `PAGE_SIZE` проектов из истории
  (по умолчанию 5) с пагинацией `« назад / N / далее »` и кнопкой
  **«🏠 В меню»**. Каждая кнопка-заголовок — это `url`-кнопка, которая
  открывает страницу проекта на FreelanceHunt.

## Архитектура

```
bot.py                     # точка входа (10 строк): configure_logging + Settings + run
app/
  config.py                # Settings из env
  logging_setup.py         # configure_logging
  app.py                   # сборка зависимостей и запуск двух потоков
  projects/model.py        # dataclass Project + (de)serialization
  source/freelancehunt.py  # FreelancehuntSource: GET /v2/projects → list[Project]
  storage/state.py         # StateStore: thread-safe persisted JSON (seen + history)
  telegram/
    client.py              # TelegramClient (sendMessage, editMessageText, getUpdates, …)
    formatting.py          # HTML-форматтеры (уведомление, шапка списка, /start)
    keyboards.py           # инлайн-клавиатуры (стартовое меню, пагинация)
    updates.py             # UpdatesPoller (long-polling, ретраи)
  handlers/
    router.py              # UpdateRouter: message → command, callback_query → callback
    commands.py            # CommandHandler: /start (и /help)
    callbacks.py           # CallbackHandler: list:N, start, noop
    views.py               # общие view-функции (text + keyboard)
  notifier/loop.py         # NotifierLoop: парс → mark seen → отправка
```

Два потока, общий `threading.Event` для shutdown, `SIGTERM`/`SIGINT`
останавливают приложение чисто.

## Состояние

Хранится в `./data/state.json` (примонтирован как том):

- `seen_ids` — ID уже отправленных проектов (последние 500);
- `projects` — последние `HISTORY_SIZE` проектов для пагинации в `/start`
  (по умолчанию 50);
- `initialized` — флаг первого запуска.
