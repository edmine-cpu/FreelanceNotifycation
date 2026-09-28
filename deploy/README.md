# Автодеплой на 173.242.51.44

```text
git push origin main → проверка сервером раз в 60 секунд → сборка → тесты → перезапуск
```

Обновляется `/opt/FreelanceNotifycation`, контейнер `fh-bots-notifier`.
Ветка `main` — источник production. Проверка запускается systemd-таймером,
поэтому GitHub Actions, SSH-секреты в GitHub и открытые webhook-порты не нужны.
Репозиторий публичный; сервер читает его по HTTPS. При переводе репозитория
в private потребуется настроить на сервере доступ на чтение.

## Что происходит после push

1. Сервер получает новый commit из `main`. Если изменений нет, контейнер не трогается.
2. Код экспортируется во временную папку. Собирается отдельный Docker-образ с SHA коммита.
3. В новом образе запускаются тесты без сети, production-данных и секретов.
   Отдельно проверяются `.env` и Compose-конфигурация. Старый бот в это время работает.
4. После успешных проверок бот останавливается, создаётся резервная копия `.env` и `data/`.
5. Сервер переключается на конкретный commit, пересоздаёт контейнер и проверяет
   успешный запуск Telegram polling и 20 секунд работы без рестартов.
6. При ошибке запуска возвращаются предыдущие код и образ. Данные автоматически
   не откатываются: это могло бы потерять новые настройки или повторить уведомления.
   Для изменений формата данных нужна обратная совместимость либо ручное восстановление копии.

Неудачный commit повторно не запускается каждую минуту. Исправь код и сделай новый push
либо используй `--retry` после исправления настроек. Сетевой сбой при получении кода
повторяется при следующем срабатывании таймера. Force-push, который убирает текущий
production commit из истории `main`, отклоняется; для отката используй `git revert`.
Изменённые вручную отслеживаемые файлы на сервере также останавливают деплой.

`.env` и `data/` находятся вне Git. Сохраняются пять последних резервных копий и
образы текущей и предыдущей версии. Удаление касается только ресурсов этого автодеплоя.
Docker запускает бота после перезагрузки сервера и перезапускает после сбоя.
Логи контейнера ограничены тремя файлами по 10 MB.

## Управление

```sh
ssh root@173.242.51.44
systemctl status freelancenotify-deploy.timer
journalctl -u freelancenotify-deploy.service -n 100 --no-pager
docker logs --tail 100 fh-bots-notifier
cat /var/lib/freelancenotify-deploy/deployed.json

# Проверить обновления немедленно
systemctl start freelancenotify-deploy.service

# Повторить попытку для commit, который раньше не прошёл проверку
/usr/local/sbin/freelancenotify-deploy --retry

# Приостановить / возобновить автоматические обновления
systemctl stop freelancenotify-deploy.timer
systemctl start freelancenotify-deploy.timer
```

Для ручного пересоздания текущего контейнера с правильным образом:

```sh
docker compose --project-name freelancenotifycation \
  --project-directory /opt/FreelanceNotifycation \
  -f /opt/FreelanceNotifycation/docker-compose.yml \
  -f /var/lib/freelancenotify-deploy/compose-image.json \
  up -d --no-build --pull never bot
```

## Установка или обновление самого механизма деплоя

Требуются Docker с Compose, Git, Python 3.12+, systemd и существующая установка бота
с контейнером `fh-bots-notifier`, `.env` и `data/`.

```sh
cd /opt/FreelanceNotifycation
sudo sh deploy/install.sh
```

Настройки путей и ветки: `/etc/default/freelancenotify-deploy`. Установленная копия
скрипта лежит в `/usr/local/sbin/freelancenotify-deploy`; после изменения `deploy/`
повтори установку. Обычные изменения бота применяются автоматически.

Проверка обработки ошибок деплоя без Docker и без production-данных:

```sh
python3 -m unittest discover -s deploy/tests -v
```
