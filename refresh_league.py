#!/usr/bin/env python3
"""Refresh public roster/waiver snapshot used by the dashboard.

This script reuses the authenticated ESPN session from scrape.py and updates
analysis.json, players/*.json and nhl_schedule.json only when their meaningful
contents actually change.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from league_export import export_league, write_snapshot
from scrape import (
    CATEGORIES,
    ROOT,
    SEASON_ID,
    get_current_scoring_period,
    get_json,
    matchup_periods,
    session,
)


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def without_updated(data: dict | None) -> dict | None:
    if data is None:
        return None

    clean = dict(data)
    clean.pop("updated", None)

    return clean


def generated_player_files(root: Path) -> set[str]:
    folder = root / "players"

    if not folder.exists():
        return set()

    return {
        f"players/{path.name}"
        for path in folder.glob("*.json")
    }


def snapshot_changed(
    root: Path,
    files: dict[str, dict],
) -> bool:

    expected_players = {
        name
        for name in files
        if name.startswith("players/")
    }

    if generated_player_files(root) != expected_players:
        return True

    for filename, content in files.items():
        existing = load_json(
            root / filename
        )

        if (
            without_updated(existing)
            != without_updated(content)
        ):
            return True

    return False


def main() -> None:
    client = session()

    base = get_json(
        client,
        params=[
            ("view", "mTeam"),
            ("view", "mSettings"),
            ("view", "mStandings"),
            ("view", "mMatchup"),
        ],
    )

    current_week = int(
        base
        .get("status", {})
        .get("currentMatchupPeriod")
        or 1
    )

    periods = matchup_periods(
        base.get("settings", {})
    )

    scoring_period = (
        get_current_scoring_period(
            client,
            base,
            current_week,
            periods,
        )
    )

    updated = datetime.now(
        ZoneInfo("Europe/Moscow")
    ).strftime(
        "%Y-%m-%d %H:%M МСК"
    )

    files = export_league(
        client,
        get_json,
        base,
        CATEGORIES,
        SEASON_ID,
        scoring_period,
        current_week,
        updated,
    )

    if not snapshot_changed(
        ROOT,
        files,
    ):
        print(
            "ESPN roster/FA snapshot checked: "
            "no changes."
        )
        return

    write_snapshot(
        ROOT,
        files,
    )

    print(
        "ESPN roster/FA snapshot updated: "
        f"week={current_week}, "
        f"scoring_period={scoring_period}"
    )


if __name__ == "__main__":
    main()
