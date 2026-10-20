# Don Cherry Cup — category dashboard

Таблица категорий, составы всех команд и полный пул FA/waivers ESPN Don Cherry Cup.

## Лига

- ESPN league ID: `1798821806`
- Season ID: `2027`
- Формат: H2H Each Category, 16 команд

## Локальный запуск

1. Создайте `.env` по примеру `.env.example` или задайте переменные окружения `ESPN_S2` и `ESPN_SWID`.
2. Установите зависимости: `python -m pip install -r requirements.txt`.
3. Запустите `python scrape.py`.
4. Откройте `index.html` через локальный HTTP-сервер, например `python -m http.server 8000`.

Значения `ESPN_S2` и `ESPN_SWID` являются секретами сессии ESPN. Их нельзя добавлять в Git, HTML, `data.json` или переписку.

## GitHub

В Settings → Secrets and variables → Actions создайте секреты:

- `ESPN_S2`
- `ESPN_SWID`

Workflow `.github/workflows/refresh.yml` проверяет ESPN и NHL примерно каждый час в :54 и публикует GitHub Pages только при изменении данных.

## Данные для анализа

- `data.json`: прежние итоги всех сыгранных недель, 16 категорий и текущие пары.
- `analysis.json`: актуальные ростеры всех команд (player ID, активный слот / BN / IR), настройки приобретений, составов, категорий, расписание матчапов и счётчики транзакций ESPN.
- `players/index.json`: имена, NHL-команды, ESPN-позиции, травмы, ONTEAM / FREEAGENT / WAIVERS, принадлежность команде и путь к подробной карточке каждого игрока.
- `players/000.json` и далее: по 100 игроков, категории и исходные статистические splits с season/source/split IDs. Прогнозы не смешиваются с фактической статистикой. Не предоставленное ESPN значение остаётся `null`.
- `nhl_schedule.json`: расписание NHL из ESPN со scoring period IDs, датами и ID команд.
- `nhl/history.json`: последние 12 матчей полевых игроков из официальных NHL Stats API; незавершённые игры помечаются и не участвуют в сигналах.
- `nhl/roster.json`: официальный NHL Playing Roster / Injury Reserve snapshot и недавние изменения.
- `nhl/player_map.json`: сопоставление NHL Player ID с ESPN Player ID по точному нормализованному полному имени с проверкой команды и позиции.
- `nhl/radar.json`: готовые Fantasy-сигналы, DCC-доступность и WATCH / ADD CANDIDATE.

Файлы публикуются в открытом репозитории вместе с таблицей. Выгрузка не содержит участников лиги, email, владельцев, личных сообщений, скрытых заявок на waivers и cookie. Все чтения ESPN выполняются с уже настроенными Actions secrets.

Обновление: каждый час в :54 (GitHub может задерживать запуск), вручную через Actions → Refresh DCC table → Run workflow, а также после изменения сборщика. Это снимок на указанное время, не непрерывный доступ к ESPN. Для рекомендаций сначала проверяйте `updated`, затем ищите игрока в индексе и читайте его `detail_file`. Снимки из разных обновлений нельзя смешивать.

`transaction_counter` сохраняет поля ESPN как они есть: сезонное число acquisitions нельзя считать использованными недельными ADD. Отсутствие поля означает неизвестное значение. Если недельный лимит устанавливается вручную регламентом, его нужно сверять с этим регламентом.

Проверка полноты: все команды должны иметь ростер; постраничная загрузка FA/waivers должна завершиться; пересечение FA и ростеров запрещено. При сбое новые данные не коммитятся и последняя успешная выгрузка сохраняется. Проверки: `python -m unittest -v test_export`.



## NHL Fantasy Radar

Радар использует официальные публичные интерфейсы NHL:

- `api.nhle.com/stats/rest/en/skater/summary`
- `api.nhle.com/stats/rest/en/skater/timeonice`
- `api.nhle.com/stats/rest/en/skater/realtime`
- `api.nhle.com/stats/rest/en/skater/faceoffpercentages`
- `api-web.nhle.com/v1/score/{date}`
- `api-web.nhle.com/v1/standings/now`
- `media.nhl.com/site/api/team/reports/roster/public`

На первом запуске собирается достаточная история текущего сезона. Далее каждый час повторно запрашиваются только последние 10 дней, чтобы ловить новые матчи и поздние статистические корректировки. На игрока хранится до 12 последних игр.

Сигнал сравнивает последние 1-2 завершённых матча с предыдущими 3-5. Рост TOI, PP/SH TOI, SOG/HIT/BLK, FOW и результативности используется как повод для проверки. API NHL не считается подтверждением конкретной пары или звена.

ESPN остаётся источником принадлежности игрока в Don Cherry Cup. В основной выдаче радара по умолчанию показываются только FREEAGENT и WAIVERS. Радар не принимает решение о дропе игроков Mighty Ducks.

Проверки: `python -m unittest -v test_export.py test_nhl_radar.py`. Live smoke test: `python nhl_radar.py --dry-run --strict`.
