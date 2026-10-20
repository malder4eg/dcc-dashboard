# Don Cherry Cup — category dashboard

Публичный дашборд ESPN Fantasy Hockey Don Cherry Cup.

## Лига

- ESPN league ID: `1798821806`
- Season ID: `2027`
- Формат: H2H Each Category, 16 команд

## Что публикуется

- `data.json`: категорийные итоги сыгранных недель и текущего матчапа.
- `analysis.json`: актуальные ростеры команд, настройки лиги и служебные данные для публичного интерфейса.
- `players/index.json` и `players/*.json`: ESPN-пул игроков, позиции, травмы, ownership, FREEAGENT / WAIVERS / ONTEAM и статистические splits.
- `nhl_schedule.json`: расписание NHL из ESPN.

Публичная часть намеренно не содержит внутренних fantasy-рекомендаций, рейтингов кандидатов, scouting-сигналов или приватной аналитики.

## Обновление

Workflow `.github/workflows/refresh.yml` проверяет ESPN каждый час в :54 и публикует GitHub Pages только при изменении данных. Ручной запуск: Actions → Refresh DCC table → Run workflow.

## Локальный запуск

1. Задайте переменные окружения `ESPN_S2` и `ESPN_SWID`.
2. Установите зависимости: `python -m pip install -r requirements.txt`.
3. Запустите `python scrape.py`.
4. Для обновления ростеров и FA/waivers запустите `python refresh_league.py`.
5. Откройте `index.html` через локальный HTTP-сервер, например `python -m http.server 8000`.

Значения `ESPN_S2` и `ESPN_SWID` являются секретами сессии ESPN. Их нельзя добавлять в Git, HTML, JSON-файлы или переписку.

Проверки: `python -m unittest -v test_export.py`.
