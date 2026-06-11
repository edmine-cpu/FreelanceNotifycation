# FreelanceHunt → Telegram

Telegram-бот в Docker. Раз в минуту через **официальное API FreelanceHunt**
(`api.freelancehunt.com/v2/projects`) берёт открытые проекты в выбранных
категориях (список `skill_id` в `SKILL_IDS`, по умолчанию «Веб-программирование»
`99` и «Разработка ботов» `180`) и присылает уведомления о новых: заголовок,
ссылка на проект, бюджет, краткое описание, время публикации и ссылка на
категорию. Каждая категория запрашивается отдельно, и проекты помечаются своей
категорией.

К каждому уведомлению бот **reply'ом** присылает черновик ставки,
сгенерированный ИИ (Gemini) в стиле твоих прошлых заявок. В строке цены и срока
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
4. Получи `GEMINI_API_KEY`: <https://aistudio.google.com/app/apikey> →
   *Create API Key*. По умолчанию используется `gemini-3.1-pro-preview`
   как рабочая замена отключенного `gemini-3-pro-preview`.
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
| `SKILL_IDS`                  | `180`              | Список ID категорий FreelanceHunt через запятую, напр. `99,180` (99 = «Веб-программирование», 180 = «Разработка ботов») |
| `POLL_INTERVAL`              | `60`               | Период опроса API в секундах                                                            |
| `STATE_FILE`                 | `/data/state.json` | Файл состояния бота                                                                     |
| `PROMPT_EXAMPLES_FILE`       | рядом с `STATE_FILE` (`bids_examples.json`) | Редактируемый через Telegram JSON с примерами промта |
| `SEND_EXISTING_ON_FIRST_RUN` | `false`            | При первом запуске прислать все имеющиеся проекты                                       |
| `PAGE_SIZE`                  | `5`                | Размер страницы в списке `/start`                                                       |
| `HISTORY_SIZE`               | `50`               | Сколько последних проектов хранить для пагинации                                        |
| `GEMINI_API_KEY`             | —                       | Ключ Gemini API. Если пустой — генерация ставок выключена                           |
| `GEMINI_MODEL`               | `gemini-3.1-pro-preview` | Имя модели Gemini                                                                  |
| `GEMINI_ENABLED`             | `true`                  | Глобальный тоггл генерации ставок                                                   |
| `GEMINI_TIMEOUT_SEC`         | `20`                    | Таймаут запроса к Gemini API                                                        |

### Добавить категорию

Допиши её `skill_id` в `SKILL_IDS` (через запятую) — URL категории и запрос к
API строятся автоматически. Для неизвестных категорий бот покажет
`Категория #<id>`, но имя можно задать без правки кода: **⚙️ Настройки → 🏷️
Имена категорий**.

## Команды бота

- `/start`, `/help` — приветствие и выбор категории: по кнопке на каждую
  категорию из `SKILL_IDS` плюс **«📋 Все»**. Открывает список последних проектов
  выбранной категории с пагинацией (история хранится по `HISTORY_SIZE` на каждую
  категорию, поэтому активная категория не вытесняет остальные). В меню есть
  **«⚙️ Настройки»**: добавление категории по `skill_id`, просмотр/замена JSON
  промта, переименование категорий и включение/отключение уведомлений по
  категориям.
- Под каждой автоматически сгенерированной ставкой —
  **«🔄 Перегенерировать»**: запрашивает новый вариант от ИИ.

## Архитектура

```
bot.py                            # точка входа: asyncio.run(run(Settings()))
app/
  config.py                       # pydantic-settings BaseSettings
  logging_setup.py                # configure_logging
  app.py                          # сборка зависимостей, asyncio.gather, signal handlers
  projects/model.py               # dataclass Project + (de)serialization
  projects/category.py            # dataclass Category (skill_id, name, listing_url)
  source/freelancehunt.py         # FreelancehuntSource: запрос по каждой категории через httpx.AsyncClient
  storage/state.py                # StateStore: asyncio.Lock + aiofiles
  telegram/
    bot.py                        # фабрика Bot + Dispatcher (aiogram 3)
    formatting.py                 # HTML-форматтеры
    keyboards.py                  # InlineKeyboardMarkup-билдеры (вкл. regen-кнопку)
    views.py                      # пары (text, keyboard) для меню и пагинации
    handlers/
      commands.py                 # Router: /start, /help
      callbacks.py                # Router: меню, настройки, списки, генерация ставок
  notifier/loop.py                # NotifierLoop: парс → отправка уведомления → reply со ставкой
  ai/
    client.py                     # GeminiClient: Gemini REST API через httpx (async)
    bid_generator.py              # few-shot prompt builder, detect_language
    prompt_store.py               # чтение/валидация редактируемого JSON промта
    prompts/
      system_prompt.md            # инструкции для модели
      bids_examples.json          # примеры ставок (few-shot)
```

Стек: `aiogram>=3.13`, `httpx>=0.27`, `pydantic-settings>=2.6`, `aiofiles`.

## Состояние

Хранится в `./data/state.json` (примонтирован как том):

- `seen_ids` — ID уже отправленных проектов (последние 500, по свежести);
- `skill_ids` — категории, добавленные через Telegram-настройки;
- `category_names` — пользовательские имена категорий, заданные через
  Telegram-настройки;
- `muted_skill_ids` — категории, по которым уведомления временно выключены;
- `projects` — последние `HISTORY_SIZE` проектов для пагинации в `/start`
  (по умолчанию 50, также используется для перегенерации ставки);
- `last_published_ts` — водяной знак времени публикации последнего обработанного
  проекта **по каждой категории** (`{skill_id: ts}`). Категория без своей записи
  считается «впервые увиденной»: её текущий бэклог гасится (или рассылается, если
  `SEND_EXISTING_ON_FIRST_RUN=true`), после чего пишется watermark. Старый формат
  (одно число) игнорируется — это безопасно (бэклог гасится, а не шлётся заново).
