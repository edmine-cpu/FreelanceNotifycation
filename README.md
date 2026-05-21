# FreelanceHunt → Telegram (Разработка ботов)

Telegram-бот в Docker. Раз в минуту через **официальное API FreelanceHunt**
(`api.freelancehunt.com/v2/projects`) берёт открытые проекты в выбранной
категории (по умолчанию — «Разработка ботов», `skill_id=180`) и присылает
уведомления о новых: заголовок, ссылка на проект, бюджет, краткое описание,
время публикации и ссылка на категорию.

К каждому уведомлению бот **reply'ом** присылает черновик ставки,
сгенерированный Gemini в стиле твоих прошлых заявок. В строке цены и срока
оставлены литералы `{price}` и `{deadline}` — подставляешь руками перед
отправкой. У каждой ставки есть кнопка **«🔄 Перегенерировать»**.

> API выбран вместо парсинга HTML: страница категории закрыта Cloudflare
> WAF, который режет запросы с дата-центровых IP. API стабильно работает
> по Bearer-токену.

## Запуск

1. Создай бота у [@BotFather](https://t.me/BotFather), получи `TELEGRAM_BOT_TOKEN`.
2. Узнай свой `TELEGRAM_CHAT_ID` (например, через [@userinfobot](https://t.me/userinfobot)).
   Напиши боту `/start` — иначе Telegram не даст слать тебе сообщения.
3. Получи `FREELANCEHUNT_TOKEN`: <https://freelancehunt.com/my/api> →
   *Personal Access Token*.
4. Получи `GEMINI_API_KEY`: <https://aistudio.google.com/apikey> →
   *Create API key*. Бесплатный тариф `gemini-2.5-flash` — 15 RPM / 1M токенов в сутки.
5. Скопируй пример конфига и заполни значения:

   ```sh
   cp .env.example .env
   ```

6. Запусти:

   ```sh
   docker compose up -d --build
   ```

7. Логи:

   ```sh
   docker compose logs -f
   ```

## Настройки (`.env`)

| Переменная                   | По умолчанию       | Описание                                                                                |
| ---------------------------- | ------------------ | --------------------------------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`         | —                  | Токен бота от BotFather                                                                 |
| `TELEGRAM_CHAT_ID`           | —                  | ID чата получателя                                                                      |
| `FREELANCEHUNT_TOKEN`        | —                  | Personal Access Token FreelanceHunt API                                                 |
| `SKILL_ID`                   | `180`              | ID категории FreelanceHunt (180 = «Разработка ботов»)                                   |
| `POLL_INTERVAL`              | `60`               | Период опроса API в секундах                                                            |
| `SEND_EXISTING_ON_FIRST_RUN` | `false`            | При первом запуске прислать все имеющиеся проекты                                       |
| `PAGE_SIZE`                  | `5`                | Размер страницы в списке `/start`                                                       |
| `HISTORY_SIZE`               | `50`               | Сколько последних проектов хранить для пагинации                                        |
| `LISTING_URL`                | (категория ботов)  | Веб-URL категории                                                                       |
| `CATEGORY_NAME`              | `Разработка ботов` | Название категории для вывода                                                           |
| `GEMINI_API_KEY`             | —                  | Ключ Google AI Studio. Если пустой — генерация ставок выключена                         |
| `GEMINI_MODEL`               | `gemini-2.5-flash` | Имя модели Gemini                                                                       |
| `GEMINI_ENABLED`             | `true`             | Глобальный тоггл генерации ставок                                                       |
| `GEMINI_TIMEOUT_SEC`         | `20`               | Таймаут запроса к Gemini API                                                            |

## Команды бота

- `/start`, `/help` — приветствие и кнопка **«📋 Последние проекты»** с
  пагинацией истории.
- Под каждой автоматически сгенерированной ставкой —
  **«🔄 Перегенерировать»**: запрашивает новый вариант от Gemini.

## Архитектура

```
bot.py                            # точка входа: asyncio.run(run(Settings()))
app/
  config.py                       # pydantic-settings BaseSettings
  logging_setup.py                # configure_logging
  app.py                          # сборка зависимостей, asyncio.gather, signal handlers
  projects/model.py               # dataclass Project + (de)serialization
  source/freelancehunt.py         # FreelancehuntSource на httpx.AsyncClient
  storage/state.py                # StateStore: asyncio.Lock + aiofiles
  telegram/
    bot.py                        # фабрика Bot + Dispatcher (aiogram 3)
    formatting.py                 # HTML-форматтеры
    keyboards.py                  # InlineKeyboardMarkup-билдеры (вкл. regen-кнопку)
    views.py                      # пары (text, keyboard) для меню и пагинации
    handlers/
      commands.py                 # Router: /start, /help
      callbacks.py                # Router: list:N, start, noop, regen:<id>
  notifier/loop.py                # NotifierLoop: парс → отправка уведомления → reply со ставкой
  gemini/
    client.py                     # тонкая обёртка над google-genai (async)
    bid_generator.py              # few-shot prompt builder, detect_language
    prompts/
      system_prompt.md            # инструкции для модели
      bids_examples.json          # примеры ставок (few-shot)
```

Стек: `aiogram>=3.13`, `httpx>=0.27`, `google-genai>=0.3`, `pydantic-settings>=2.6`, `aiofiles`.

## Состояние

Хранится в `./data/state.json` (примонтирован как том):

- `seen_ids` — ID уже отправленных проектов (последние 500);
- `projects` — последние `HISTORY_SIZE` проектов для пагинации в `/start`
  (по умолчанию 50, также используется для перегенерации ставки);
- `initialized` — флаг первого запуска;
- `last_published_ts` — водяной знак времени публикации последнего обработанного проекта.
