#!/usr/bin/env python3
"""Download private ESPN DCC matchup totals and write data.json.

Authentication is supplied only through ESPN_S2 and ESPN_SWID environment
variables. Secrets are never written to disk or included in output.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


LEAGUE_ID = 1798821806
SEASON_ID = 2027
ROOT = Path(__file__).resolve().parent
API = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fhl/"
    f"seasons/{SEASON_ID}/segments/0/leagues/{LEAGUE_ID}"
)

# ESPN hockey stat IDs. Hidden goalie components are retained so season GAA
# and SV% can be recomputed correctly instead of averaging weekly ratios.
CATEGORIES = [
    {"key": "G", "label": "G", "stat": "13", "lower": False, "format": "int"},
    {"key": "A", "label": "A", "stat": "14", "lower": False, "format": "int"},
    {"key": "PTS", "label": "PTS", "stat": "16", "lower": False, "format": "int"},
    {"key": "+/-", "label": "+/-", "stat": "15", "lower": False, "format": "int"},
    {"key": "PPP", "label": "PPP", "stat": "38", "lower": False, "format": "int"},
    {"key": "SHP", "label": "SHP", "stat": "39", "lower": False, "format": "int"},
    {"key": "FOW", "label": "FOW", "stat": "23", "lower": False, "format": "int"},
    {"key": "TOI", "label": "TOI", "stat": "26", "lower": False, "format": "time"},
    {"key": "SOG", "label": "SOG", "stat": "29", "lower": False, "format": "int"},
    {"key": "HIT", "label": "HIT", "stat": "31", "lower": False, "format": "int"},
    {"key": "BLK", "label": "BLK", "stat": "32", "lower": False, "format": "int"},
    {"key": "W", "label": "W", "stat": "1", "lower": False, "format": "int"},
    {"key": "SV", "label": "SV", "stat": "6", "lower": False, "format": "int"},
    {"key": "SO", "label": "SO", "stat": "7", "lower": False, "format": "int"},
    {"key": "GAA", "label": "GAA", "stat": "10", "lower": True, "format": "ratio2"},
    {"key": "SV%", "label": "SV%", "stat": "11", "lower": False, "format": "ratio3"},
]


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задан обязательный секрет {name}")
    return value


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update({"User-Agent": "DCCCategoryDashboard/1.0"})
    client.cookies.update({"espn_s2": required_env("ESPN_S2"), "SWID": required_env("ESPN_SWID")})
    return client


def get_json(client: requests.Session, *, params: list[tuple[str, str]], headers: dict | None = None) -> dict:
    response = client.get(API, params=params, headers=headers or {}, timeout=45)
    if response.status_code == 401:
        raise RuntimeError("ESPN отклонил авторизацию. Обновите ESPN_S2 и ESPN_SWID.")
    response.raise_for_status()
    payload = response.json()
    return payload[0] if isinstance(payload, list) else payload


def team_name(team: dict) -> str:
    if team.get("name"):
        return team["name"]
    full = " ".join(x for x in (team.get("location"), team.get("nickname")) if x)
    return full.strip() or team.get("abbrev") or f"Team {team['id']}"


def matchup_periods(settings: dict) -> dict[int, list[int]]:
    raw = settings.get("scheduleSettings", {}).get("matchupPeriods", {})
    return {int(k): [int(x) for x in value] for k, value in raw.items()}


def side_stats(side: dict) -> tuple[dict[str, float | None], dict[str, float | None], bool]:
    cumulative = side.get("cumulativeScore") or {}
    score_by_stat = cumulative.get("scoreByStat") or {}
    stats: dict[str, float | None] = {}
    raw: dict[str, float | None] = {}
    for stat_id, item in score_by_stat.items():
        raw[str(stat_id)] = item.get("score")
    for cat in CATEGORIES:
        stats[cat["key"]] = raw.get(cat["stat"])
    ineligible = any(
        bool((score_by_stat.get(cat["stat"]) or {}).get("ineligible"))
        for cat in CATEGORIES
        if cat["key"] in {"GAA", "SV%"}
    )
    return stats, raw, ineligible


def fetch_week(
    client: requests.Session,
    week: int,
    scoring_period: int,
    names: dict[int, str],
) -> dict:
    fantasy_filter = {"schedule": {"filterMatchupPeriodIds": {"value": [week]}}}
    data = get_json(
        client,
        params=[
            ("view", "mMatchupScore"),
            ("view", "mScoreboard"),
            ("scoringPeriodId", str(scoring_period)),
        ],
        headers={"x-fantasy-filter": json.dumps(fantasy_filter)},
    )
    teams: dict[int, dict] = {}
    pairs: list[list[str]] = []
    for matchup in data.get("schedule", []):
        if int(matchup.get("matchupPeriodId", 0)) != week:
            continue
        pair: list[str] = []
        for side_key in ("home", "away"):
            side = matchup.get(side_key) or {}
            team_id = side.get("teamId")
            if not team_id or team_id not in names:
                continue
            stats, raw, ineligible = side_stats(side)
            teams[team_id] = {
                "id": team_id,
                "name": names[team_id],
                "stats": stats,
                "raw": raw,
                "ineligible_goalies": ineligible,
            }
            pair.append(names[team_id])
        if len(pair) == 2:
            pairs.append(pair)
    # ESPN can omit untouched teams from an early empty period.
    for team_id, name in names.items():
        teams.setdefault(
            team_id,
            {
                "id": team_id,
                "name": name,
                "stats": {cat["key"]: None for cat in CATEGORIES},
                "raw": {},
                "ineligible_goalies": False,
            },
        )
    return {"week": week, "teams": list(teams.values()), "pairs": pairs}


def load_previous() -> dict:
    path = ROOT / "data.json"
    if not path.exists():
        return {"weeks": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    client = session()
base = get_json(
    client,
    params=[
        ("view", "mTeam"),
        ("view", "mSettings"),
        ("view", "mStandings"),
        ("view", "mMatchup"),
        ("view", "mStatus"),
    ],
)

names = {int(team["id"]): team_name(team) for team in base.get("teams", [])}

status = base.get("status", {})
current = int(status.get("currentMatchupPeriod") or 1)

periods = matchup_periods(base.get("settings", {}))
current_period_ids = periods.get(current, [])

# ESPN hockey separates matchup periods (weeks)
# from scoring periods (individual days).
candidates = [
    status.get("currentScoringPeriod"),
    base.get("scoringPeriodId"),
    status.get("latestScoringPeriod"),
]

current_scoring = None

for value in candidates:
    if value is None:
        continue
    value = int(value)
    if not current_period_ids or value in current_period_ids:
        current_scoring = value
        break

# Safe fallback: use the latest scoring period belonging
# to the current matchup instead of silently falling back to 1.
if current_scoring is None:
    latest = status.get("latestScoringPeriod")

    if latest is not None:
        latest = int(latest)
        valid = [x for x in current_period_ids if x <= latest]
        if valid:
            current_scoring = max(valid)

if current_scoring is None:
    current_scoring = min(current_period_ids) if current_period_ids else current

print(
    f"ESPN periods: matchup={current}, "
    f"scoring={current_scoring}, "
    f"latest={status.get('latestScoringPeriod')}, "
    f"week_periods={current_period_ids}"
)
    previous = load_previous()
    weeks = dict(previous.get("weeks", {}))

    # Completed weeks are immutable in practice; refresh the current period and
    # backfill any missing earlier periods.
    for week in range(1, current + 1):
        if str(week) in weeks and week < current:
            continue
        scoring_ids = periods.get(week, [week])
        scoring_period = current_scoring if week == current else max(scoring_ids)
        weeks[str(week)] = fetch_week(client, week, scoring_period, names)

    payload = {
        "league": base.get("settings", {}).get("name", "Don Cherry Cup"),
        "league_id": LEAGUE_ID,
        "season_id": SEASON_ID,
        "updated": datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M МСК"),
        "current_week": current,
        "categories": [
            {k: cat[k] for k in ("key", "label", "lower", "format")} for cat in CATEGORIES
        ],
        "weeks": weeks,
    }
    (ROOT / "data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Обновлено: {payload['league']}, неделя {current}, команд {len(names)}")


if __name__ == "__main__":
    main()

