# Don Cherry Cup — category dashboard

Ежедневная таблица категорий ESPN Don Cherry Cup.

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

Workflow `.github/workflows/refresh.yml` обновляет `data.json` ежедневно и публикует страницу через GitHub Pages.

