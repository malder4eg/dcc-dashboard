#!/usr/bin/env python3
"""Daily DCC skater impact ranking. Uses the NHL collector's verified game history.

Every DCC category contributes separately. Scores are relative to the 90th
percentile of recent NHL skater-games, with no category caps: every
additional goal, assist, faceoff win, hit and block counts. ESPN files are the
ownership authority.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
RADAR_DIR = ROOT / "ducks-radar-7c91e4f2"
OUTPUT = RADAR_DIR / "daily_impact.json"
CATEGORIES = ("g", "a", "pts", "plus_minus", "ppp", "shp", "fow", "toi", "sog", "hit", "blk")
FINAL = {"OFF", "FINAL"}
MSK = ZoneInfo("Europe/Moscow")


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    at = (len(ordered) - 1) * fraction
    lo = math.floor(at)
    hi = math.ceil(at)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (at - lo)


def benchmarks_from_history(history: dict) -> dict[str, float]:
    rows = [
        game
        for player in history.get("players", {}).values()
        for game in player.get("games", [])
        if game.get("final") and player.get("position") != "G"
    ]
    result = {}
    for category in CATEGORIES:
        values = [max(0.0, float(g.get(category) or 0)) for g in rows]
        # A zero-heavy category (SHP, PPP) still gets a nonzero denominator.
        result[category] = max(1.0, percentile(values, 0.90))
    # TOI is measured in seconds; the minimum keeps early-season samples sane.
    result["toi"] = max(900.0, result["toi"])
    return result


def calculate_impact(game: dict, benchmarks: dict[str, float]) -> tuple[float, dict, int]:
    # No upper cap, including for negative +/-: every additional category unit
    # has the same normalized value. Preserve the existing category weights.
    components = {}
    for category in CATEGORIES:
        value = float(game.get(category) or 0)
        contribution = value / benchmarks[category]
        if category != "plus_minus":
            contribution = max(0.0, contribution)
        components[category] = round(contribution, 3)
    breadth = sum(
        components[key] >= 0.5
        for key in CATEGORIES if key not in ("toi", "plus_minus")
    )
    # Small breadth bonus, with no arbitrary cap.
    score = sum(components.values()) + 0.10 * breadth
    return round(score, 3), components, breadth


def normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", name.lower())


def safe_espn_fallback(nhl_name: str, nhl_team: str,
                       index_by_name: dict, team_ids: dict) -> dict:
    """Resolve unmapped skaters only by UNIQUE full name and matching NHL team.

    Uses team IDs inferred from already verified NHL/ESPN mappings. A name
    collision, missing team, or mismatched team remains UNVERIFIED.
    """
    candidates = index_by_name.get(normalize_name(nhl_name), [])
    expected_team = team_ids.get(nhl_team)
    if len(candidates) != 1 or expected_team is None:
        return {}
    candidate = candidates[0]
    if int(candidate.get("pro_team_id") or -1) != expected_team:
        return {}
    return candidate


def scoreboard(date_string: str) -> list[dict]:
    url = "https://api-web.nhle.com/v1/score/" + date_string
    response = requests.get(url, timeout=35, headers={"User-Agent": "DCCDailyImpact/1.0"})
    response.raise_for_status()
    data = response.json()
    games = data.get("games")
    if not isinstance(games, list):
        raise RuntimeError("NHL score endpoint has no games list")
    return games


def build(date_string: str, history: dict, mapping: dict, index: dict,
          analysis: dict, scheduled: list[dict], error: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    expected = {int(g["id"]): g for g in scheduled if g.get("id") is not None}
    finals = {gid: g for gid, g in expected.items() if g.get("gameState") in FINAL}
    pending = [gid for gid in expected if gid not in finals]
    by_nhl_id = {
        int(p["nhl_player_id"]): p
        for p in mapping.get("mapped", []) if p.get("nhl_player_id") is not None
    }
    by_espn_id = {int(p["id"]): p for p in index.get("players", [])}
    index_by_name = defaultdict(list)
    for player in index.get("players", []):
        index_by_name[normalize_name(player.get("name"))].append(player)
    team_votes = defaultdict(Counter)
    for player in mapping.get("mapped", []):
        nhl_team = player.get("nhl_team")
        pro_team_id = player.get("espn_pro_team_id")
        if nhl_team and pro_team_id is not None:
            team_votes[nhl_team][int(pro_team_id)] += 1
    # Do not infer a team when the evidence is ambiguous.
    team_ids = {
        team: counts.most_common(1)[0][0]
        for team, counts in team_votes.items()
        if len(counts) == 1 or (
            counts.most_common(1)[0][1] > 2 * counts.most_common(2)[1][1]
        )
    }
    owners = {int(t["id"]): t.get("name", "") for t in analysis.get("teams", [])}
    benchmarks = benchmarks_from_history(history)
    rankings = []
    counts = {gid: 0 for gid in finals}
    seen = set()

    for player_id, player in history.get("players", {}).items():
        if player.get("position") == "G":
            continue
        for game in player.get("games", []):
            gid = int(game.get("game_id") or 0)
            if gid not in finals or game.get("game_date") != date_string or not game.get("final"):
                continue
            unique = (gid, int(player_id))
            if unique in seen:
                continue
            seen.add(unique)
            counts[gid] += 1
            score, components, breadth = calculate_impact(game, benchmarks)
            mapped = by_nhl_id.get(int(player_id), {})
            espn = by_espn_id.get(int(mapped.get("espn_player_id") or 0), {})
            if not espn:
                espn = safe_espn_fallback(
                    game.get("name") or player.get("name"), game.get("team"),
                    index_by_name, team_ids,
                )
            status = espn.get("availability", "UNVERIFIED")
            owner_id = espn.get("on_team_id")
            owner = owners.get(int(owner_id)) if owner_id else None
            stats = {key: game.get(key) for key in CATEGORIES}
            stats.update({
                "ev_toi": game.get("ev_toi"),
                "pp_toi": game.get("pp_toi"),
                "sh_toi": game.get("sh_toi"),
                "fo": game.get("fo"),
                "shifts": game.get("shifts"),
            })
            rankings.append({
                "rank": 0,
                "name": game.get("name") or player.get("name"),
                "nhl_player_id": int(player_id),
                "nhl_team": game.get("team"),
                "opponent": game.get("opponent"),
                "position": game.get("position") or player.get("position"),
                "game_id": gid,
                "game_date": date_string,
                "impact_score": score,
                "category_impact": components,
                "breadth": breadth,
                "stats": stats,
                "espn_player_id": espn.get("id"),
                "availability": status,
                "on_team_id": owner_id,
                "owner": owner,
                "is_mighty_ducks": owner_id is not None and int(owner_id) == 12,
            })

    rankings.sort(key=lambda p: (
        -p["impact_score"], -p["breadth"],
        -(p["stats"].get("pts") or 0), p["name"] or "",
    ))
    for i, player in enumerate(rankings, 1):
        player["rank"] = i
    incomplete_games = [
        {"game_id": gid, "skaters_found": count,
         "home": finals[gid].get("homeTeam", {}).get("abbrev"),
         "away": finals[gid].get("awayTeam", {}).get("abbrev")}
        for gid, count in sorted(counts.items()) if count < 34
    ]
    coverage = {
        "scheduled_games": len(expected),
        "final_games": len(finals),
        "pending_games": len(pending),
        "pending_game_ids": pending,
        "skaters_ranked": len(rankings),
        "final_game_skaters": {str(k): v for k, v in sorted(counts.items())},
        "incomplete_final_games": incomplete_games,
        "nhl_mapping_coverage": mapping.get("coverage"),
        "espn_index_complete": index.get("complete"),
        "espn_analysis_complete": analysis.get("complete"),
        "scoreboard_error": error,
    }
    complete = (
        error is None and not pending and not incomplete_games
        and bool(index.get("complete")) and bool(analysis.get("complete"))
        and (not finals or bool(rankings))
    )
    return {
        "schema_version": 2,
        "game_date": date_string,
        "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "updated": now.astimezone(MSK).strftime("%Y-%m-%d %H:%M МСК"),
        "complete": complete,
        "history_updated": history.get("updated"),
        "mapping_updated": mapping.get("updated"),
        "espn_updated": index.get("updated"),
        "source": {
            "game_status": "https://api-web.nhle.com/v1/score/" + date_string,
            "game_stats": "https://api.nhle.com/stats/rest/en/skater/",
            "espn": "players/index.json + analysis.json",
        },
        "method": {
            "description": "Uncapped sum of per-category results / recent NHL skater-game p90; negative plus-minus subtracts points; small breadth bonus. All 11 DCC categories scored separately.",
            "categories": list(CATEGORIES),
            "category_cap": None,
            "breadth_bonus": "0.1 for each non-TOI, non-plus-minus category with impact >= 0.5; no cap",
            "benchmarks_p90": {k: round(v, 2) for k, v in benchmarks.items()},
            "not_included": ["goalies", "xG", "line combinations"],
        },
        "coverage": coverage,
        "top3": rankings[:3],
        "top20": rankings[:20],
        "top3_available": [p for p in rankings if p["availability"] in ("FREEAGENT", "WAIVERS")][:3],
        "top3_freeagents": [p for p in rankings if p["availability"] == "FREEAGENT"][:3],
        "best_mighty_ducks": [p for p in rankings if p["is_mighty_ducks"]][:3],
        "rankings": rankings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="NHL local game date YYYY-MM-DD; default: previous Moscow date")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    game_date = args.date or (datetime.now(MSK).date() - timedelta(days=1)).isoformat()
    history = load(RADAR_DIR / "history.json")
    mapping = load(RADAR_DIR / "player_map.json")
    index = load(ROOT / "players" / "index.json")
    analysis = load(ROOT / "analysis.json")
    try:
        games = scoreboard(game_date)
        error = None
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        games = []
        error = str(exc)
        print("::warning::Could not verify NHL game schedule: " + error)
    snapshot = build(game_date, history, mapping, index, analysis, games, error)
    summary = snapshot["coverage"]
    print(
        "DCC daily impact: " + game_date +
        ", complete=" + str(snapshot["complete"]) +
        ", final=" + str(summary["final_games"]) +
        ", pending=" + str(summary["pending_games"]) +
        ", skaters=" + str(summary["skaters_ranked"]) +
        ", missing_games=" + str(len(summary["incomplete_final_games"]))
    )
    for player in snapshot["top3"]:
        print("TOP " + str(player["rank"]) + ": " + str(player["name"]) +
              " " + str(player["impact_score"]) + " " + str(player["availability"]))
    if not snapshot["complete"]:
        print("::warning::Daily impact is partial; do not describe it as a complete NHL ranking.")
    if not args.dry_run:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with OUTPUT.open("w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2)
            stream.write("\n")


if __name__ == "__main__":
    main()
