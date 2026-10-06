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


# ESPN hockey stat IDs.
# Hidden goalie components are retained so season GAA and SV%
# can be recomputed correctly instead of averaging weekly ratios.
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


# These stats are used only to detect whether ESPN has returned
# real matchup data. GAA is deliberately excluded because an empty
# goalie stat line may be represented as Infinity.
REAL_DATA_KEYS = {
    "G",
    "A",
    "PTS",
    "PPP",
    "SHP",
    "FOW",
    "TOI",
    "SOG",
    "HIT",
    "BLK",
    "W",
    "SV",
    "SO",
}


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()

    if not value:
        raise RuntimeError(f"Не задан обязательный секрет {name}")

    return value


def session() -> requests.Session:
    client = requests.Session()

    client.headers.update(
        {
            "User-Agent": "DCCCategoryDashboard/1.0",
        }
    )

    client.cookies.update(
        {
            "espn_s2": required_env("ESPN_S2"),
            "SWID": required_env("ESPN_SWID"),
        }
    )

    return client


def get_json(
    client: requests.Session,
    *,
    params: list[tuple[str, str]],
    headers: dict | None = None,
) -> dict:
    response = client.get(
        API,
        params=params,
        headers=headers or {},
        timeout=45,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "ESPN отклонил авторизацию. "
            "Обновите ESPN_S2 и ESPN_SWID."
        )

    response.raise_for_status()

    payload = response.json()

    return payload[0] if isinstance(payload, list) else payload


def team_name(team: dict) -> str:
    if team.get("name"):
        return team["name"]

    full = " ".join(
        x
        for x in (
            team.get("location"),
            team.get("nickname"),
        )
        if x
    )

    return (
        full.strip()
        or team.get("abbrev")
        or f"Team {team['id']}"
    )


def matchup_periods(settings: dict) -> dict[int, list[int]]:
    raw = (
        settings
        .get("scheduleSettings", {})
        .get("matchupPeriods", {})
    )

    return {
        int(key): [int(x) for x in value]
        for key, value in raw.items()
    }


def side_stats(
    side: dict,
) -> tuple[
    dict[str, float | None],
    dict[str, float | None],
    bool,
]:
    cumulative = side.get("cumulativeScore") or {}
    score_by_stat = cumulative.get("scoreByStat") or {}

    stats: dict[str, float | None] = {}
    raw: dict[str, float | None] = {}

    for stat_id, item in score_by_stat.items():
        raw[str(stat_id)] = item.get("score")

    for cat in CATEGORIES:
        stats[cat["key"]] = raw.get(cat["stat"])

    ineligible = any(
        bool(
            (score_by_stat.get(cat["stat"]) or {})
            .get("ineligible")
        )
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
    fantasy_filter = {
        "schedule": {
            "filterMatchupPeriodIds": {
                "value": [week]
            }
        }
    }

    data = get_json(
        client,
        params=[
            ("view", "mMatchupScore"),
            ("view", "mScoreboard"),
            ("scoringPeriodId", str(scoring_period)),
        ],
        headers={
            "x-fantasy-filter": json.dumps(fantasy_filter)
        },
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

            if not team_id:
                continue

            team_id = int(team_id)

            if team_id not in names:
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
                "stats": {
                    cat["key"]: None
                    for cat in CATEGORIES
                },
                "raw": {},
                "ineligible_goalies": False,
            },
        )

    return {
        "week": week,
        "teams": list(teams.values()),
        "pairs": pairs,
    }


def has_real_stats(week_data: dict) -> bool:
    """Return True if ESPN returned any meaningful non-zero matchup stat."""

    for team in week_data.get("teams", []):
        stats = team.get("stats", {})

        for key in REAL_DATA_KEYS:
            value = stats.get(key)

            if value is None:
                continue

            try:
                if float(value) != 0:
                    return True
            except (TypeError, ValueError):
                continue

    return False


def get_current_scoring_period(
    client: requests.Session,
    base: dict,
    current_week: int,
    periods: dict[int, list[int]],
) -> int:
    """Determine ESPN's active scoring period.

    Hockey matchup periods are weekly, while scoring periods are daily.
    mScoreboard is therefore queried separately instead of assuming that
    scoringPeriodId equals the matchup/week number.
    """

    scoreboard = get_json(
        client,
        params=[
            ("view", "mScoreboard"),
        ],
    )

    week_periods = periods.get(current_week, [])

    candidates = [
        scoreboard.get("scoringPeriodId"),
        base.get("scoringPeriodId"),
        base.get("status", {}).get("currentScoringPeriod"),
    ]

    for candidate in candidates:
        if candidate is None:
            continue

        try:
            scoring_period = int(candidate)
        except (TypeError, ValueError):
            continue

        if not week_periods or scoring_period in week_periods:
            print(
                "ESPN periods: "
                f"matchup={current_week}, "
                f"scoring={scoring_period}, "
                f"week_periods={week_periods}"
            )

            return scoring_period

    latest = base.get("status", {}).get("latestScoringPeriod")

    if latest is not None:
        try:
            latest = int(latest)
        except (TypeError, ValueError):
            latest = None

    if week_periods and latest is not None:
        # latestScoringPeriod can mean the most recently completed day.
        # The active scoring period can therefore be latest + 1.
        possible = [
            period
            for period in week_periods
            if period <= latest + 1
        ]

        if possible:
            scoring_period = max(possible)

            print(
                "ESPN periods fallback: "
                f"matchup={current_week}, "
                f"scoring={scoring_period}, "
                f"latest={latest}, "
                f"week_periods={week_periods}"
            )

            return scoring_period

    if week_periods:
        scoring_period = min(week_periods)

        print(
            "ESPN periods fallback to first day: "
            f"matchup={current_week}, "
            f"scoring={scoring_period}, "
            f"week_periods={week_periods}"
        )

        return scoring_period

    raise RuntimeError(
        "Не удалось определить текущий ESPN scoringPeriodId."
    )


def fetch_current_week(
    client: requests.Session,
    week: int,
    scoring_period: int,
    scoring_ids: list[int],
    names: dict[int, str],
) -> tuple[dict, int]:
    """Fetch current matchup and fall back to earlier daily periods if needed."""

    candidates: list[int] = [scoring_period]

    candidates.extend(
        sorted(
            (
                period
                for period in scoring_ids
                if period < scoring_period
            ),
            reverse=True,
        )
    )

    # Remove duplicates while preserving order.
    seen: set[int] = set()
    candidates = [
        period
        for period in candidates
        if not (
            period in seen
            or seen.add(period)
        )
    ]

    last_result: dict | None = None

    for candidate in candidates:
        print(
            f"Проверяем matchup {week}, "
            f"scoring period {candidate}"
        )

        result = fetch_week(
            client,
            week,
            candidate,
            names,
        )

        last_result = result

        if has_real_stats(result):
            print(
                f"Найдены данные ESPN: "
                f"matchup {week}, "
                f"scoring period {candidate}"
            )

            return result, candidate

    if last_result is not None:
        print(
            f"ESPN пока вернул нулевую статистику "
            f"для matchup {week}."
        )

        return last_result, scoring_period

    raise RuntimeError(
        f"Не удалось загрузить данные текущего matchup {week}."
    )


def load_previous() -> dict:
    path = ROOT / "data.json"

    if not path.exists():
        return {"weeks": {}}

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def main() -> None:
    client = session()

    # This request is already known to work for the private DCC league.
    base = get_json(
        client,
        params=[
            ("view", "mTeam"),
            ("view", "mSettings"),
            ("view", "mStandings"),
            ("view", "mMatchup"),
        ],
    )

    names = {
        int(team["id"]): team_name(team)
        for team in base.get("teams", [])
    }

    if not names:
        raise RuntimeError(
            "ESPN не вернул список команд лиги."
        )

    status = base.get("status", {})

    current = int(
        status.get("currentMatchupPeriod")
        or 1
    )

    periods = matchup_periods(
        base.get("settings", {})
    )

    current_scoring = get_current_scoring_period(
        client,
        base,
        current,
        periods,
    )

    previous = load_previous()

    weeks = dict(
        previous.get("weeks", {})
    )

    # Completed weeks are effectively immutable.
    # Refresh only the current period and backfill missing older periods.
    for week in range(1, current + 1):
        if str(week) in weeks and week < current:
            continue

        scoring_ids = periods.get(
            week,
            [week],
        )

        if week == current:
            week_data, used_scoring_period = fetch_current_week(
                client=client,
                week=week,
                scoring_period=current_scoring,
                scoring_ids=scoring_ids,
                names=names,
            )

            weeks[str(week)] = week_data

            print(
                f"Текущая неделя {week}: "
                f"использован scoring period "
                f"{used_scoring_period}"
            )

        else:
            scoring_period = max(scoring_ids)

            weeks[str(week)] = fetch_week(
                client,
                week,
                scoring_period,
                names,
            )

    payload = {
        "league": (
            base
            .get("settings", {})
            .get("name", "Don Cherry Cup")
        ),
        "league_id": LEAGUE_ID,
        "season_id": SEASON_ID,
        "updated": datetime.now(
            ZoneInfo("Europe/Moscow")
        ).strftime(
            "%Y-%m-%d %H:%M МСК"
        ),
        "current_week": current,
        "categories": [
            {
                key: cat[key]
                for key in (
                    "key",
                    "label",
                    "lower",
                    "format",
                )
            }
            for cat in CATEGORIES
        ],
        "weeks": weeks,
    }

    output_path = ROOT / "data.json"

    output_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Обновлено: {payload['league']}, "
        f"неделя {current}, "
        f"команд {len(names)}"
    )


if __name__ == "__main__":
    main()
