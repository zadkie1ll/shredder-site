# Shredder Site

Сайт и личный кабинет Shredder VPN.

Схема базы данных не описывается в этом проекте. Таблицы, модели и миграции живут
в общем модуле `common`, как и в остальных сервисах Shredder.

## Локальный запуск

```bash
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

После старта сайт доступен на `http://127.0.0.1:8010`.

Без `.env` сайт работает в демо-режиме:

```text
username: demo
password: demo12345
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

В `.env` нужно указать:

- `SHREDDER_SITE_SESSION_SECRET` — секрет для cookies-сессии;
- `SHREDDER_SITE_LOGIN_PASSWORD` — временный общий пароль входа в кабинет;
- `SHREDDER_SITE_TRIAL_PERIOD_DAYS` — срок пробного доступа при регистрации, по умолчанию 7 дней;
- `SHREDDER_SITE_YOOKASSA_SHOP_ID` / `SHREDDER_SITE_YOOKASSA_SECRET` — доступ для создания YooKassa-платежей;
- `SHREDDER_SITE_RECEIPT_EMAIL` — email для чеков YooKassa;
- `SHREDDER_SITE_DATABASE_URL` или `MI_VPN_BOT_POSTGRES_*` — доступ к общей Postgres-базе;
- `SHREDDER_SITE_RWMS_ADDR` / `SHREDDER_SITE_RWMS_PORT` или `MI_VPN_BOT_RWMS_*` — gRPC endpoint RWMS/Remnawave.
- `SHREDDER_SITE_INTERNAL_SQUADS_UUIDS` — список squad UUID через запятую, если RWMS должен сразу привязать пользователя к squad.

`common` должен быть доступен в директории проекта перед сборкой образа.

## Что уже есть

- главная страница с тарифами;
- страница логина;
- регистрация, которая создает пользователя в RWMS/Remnawave и выдает пробный доступ;
- личный кабинет с установкой, подпиской и рефералами;
- создание YooKassa-платежа по тарифам из `common`;
- чтение пользователя и рефералов через модели `common`;
- получение ссылки подписки из RWMS/Remnawave по `username`;
- docker-compose для запуска сайта.
