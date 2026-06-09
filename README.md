# Shredder Site

Сайт и личный кабинет Shredder.

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
- `SHREDDER_SITE_LOGIN_PASSWORD` — legacy fallback для пользователей без личного пароля;
- `SHREDDER_SITE_TRIAL_PERIOD_DAYS` — срок пробного доступа при регистрации, по умолчанию 7 дней;
- `SHREDDER_SITE_REGISTRATION_CODE_TTL_SECONDS` — срок действия email-кода регистрации, по умолчанию 900 секунд;
- `SHREDDER_SITE_YOOKASSA_SHOP_ID` / `SHREDDER_SITE_YOOKASSA_SECRET` — доступ для создания YooKassa-платежей;
- `SHREDDER_SITE_RECEIPT_EMAIL` — email для receipt в YooKassa, по умолчанию как в боте `receipts@orpheous.ru`;
- `SHREDDER_SITE_SMTP_HOST`, `SHREDDER_SITE_SMTP_PORT`, `SHREDDER_SITE_SMTP_USERNAME`, `SHREDDER_SITE_SMTP_PASSWORD`, `SHREDDER_SITE_SMTP_FROM_EMAIL`, `SHREDDER_SITE_SMTP_USE_TLS` — SMTP для отправки кодов регистрации;
- `SHREDDER_SITE_TELEGRAM_BOT_USERNAME` / `SHREDDER_SITE_TELEGRAM_BOT_TOKEN` — бот для Telegram Login Widget, входа через Telegram и проверки подписи привязки;
- `SHREDDER_SITE_TELEGRAM_LINK_BONUS_DAYS` — бонус за привязку Telegram, по умолчанию 7 дней;
- `SHREDDER_SITE_YANDEX_CLIENT_ID` — приложение Яндекс OAuth для входа через виджет; Redirect URI для виджета: `${SHREDDER_SITE_PUBLIC_BASE_URL}/auth/yandex/token`;
- `SHREDDER_SITE_YANDEX_CLIENT_SECRET` — опционально, нужен только для fallback Redirect URI `${SHREDDER_SITE_PUBLIC_BASE_URL}/auth/yandex/callback`;
- `SHREDDER_SITE_YANDEX_SCOPES` — права Яндекс OAuth, по умолчанию `login:info,login:email`;
- `SHREDDER_SITE_GOOGLE_CLIENT_ID` / `SHREDDER_SITE_GOOGLE_CLIENT_SECRET` — приложение Google OAuth типа Web application; в Authorized redirect URIs нужно дословно добавить `${SHREDDER_SITE_PUBLIC_BASE_URL}/auth/google/callback` без завершающего `/`;
- `SHREDDER_SITE_GOOGLE_SCOPES` — права Google OAuth, по умолчанию `openid email profile`;
- `SHREDDER_SITE_ONE_CLICK_REDIRECT_URL` — redirect-префикс для one-click установки;
- `SHREDDER_SITE_DATABASE_URL` — доступ к общей Postgres-базе;
- `SHREDDER_SITE_RWMS_ADDR` / `SHREDDER_SITE_RWMS_PORT` — gRPC endpoint RWMS/Remnawave.
- `SHREDDER_SITE_LEGACY_LIMITED_SUBSCRIPTION_ENABLED` — включает legacy-режим урезанной подписки через internal squads, по умолчанию выключен;
- `SHREDDER_SITE_INTERNAL_SQUADS_UUIDS` — legacy-список squad UUID через запятую, применяется только если включен `SHREDDER_SITE_LEGACY_LIMITED_SUBSCRIPTION_ENABLED`.

`common` должен быть доступен в директории проекта перед сборкой образа.

## Что уже есть

- главная страница с тарифами;
- страница логина;
- вход через Telegram для пользователей бота и сайта;
- вход через Яндекс OAuth со склейкой аккаунта по email;
- регистрация по почте, паролю и коду на почту, которая создает пользователя в RWMS/Remnawave и выдает пробный доступ;
- личный кабинет с установкой, подпиской и рефералами;
- создание YooKassa-платежа по тарифам из `common`;
- чтение пользователя и рефералов через модели `common`;
- получение ссылки подписки из RWMS/Remnawave по `username`;
- docker-compose для запуска сайта.
