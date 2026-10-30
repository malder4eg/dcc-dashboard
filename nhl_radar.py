#!/usr/bin/env python3
"""Build an NHL Fantasy Radar snapshot for the DCC dashboard.

Public NHL APIs provide real-game usage and stats. ESPN remains the authority
for DCC ownership / availability. The collector keeps only a rolling per-player
history, re-fetches recent days for stat corrections, excludes non-final games
from signals, and never guesses line combinations from TOI alone.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent
NHL_DIR = ROOT / "ducks-radar-7c91e4f2"
HISTORY_PATH = NHL_DIR / "history.json"
ROSTER_PATH = NHL_DIR / "roster.json"
MAP_PATH = NHL_DIR / "player_map.json"
RADAR_PATH = NHL_DIR / "radar.json"

NHL_SEASON_ID = 20262027
GAME_TYPE_ID = 2
REFRESH_DAYS = 10
HISTORY_GAMES = 12
RECENT_EVENT_DAYS = 7

STATS_BASE = "https://api.nhle.com/stats/rest/en/skater"
WEB_BASE = "https://api-web.nhle.com/v1"
ROSTER_REPORT_URL = "https://media.nhl.com/site/api/team/reports/roster/public"

FINAL_STATES = {"OFF", "FINAL"}
AVAILABLE_STATUSES = {"FREEAGENT", "WAIVERS"}

REPORT_FIELDS = {
    "summary": {
        "goals": "g",
        "assists": "a",
        "points": "pts",
        "plusMinus": "plus_minus",
        "ppPoints": "ppp",
        "shPoints": "shp",
        "shots": "sog",
    },
    "timeonice": {
        "timeOnIce": "toi",
        "evTimeOnIce": "ev_toi",
        "ppTimeOnIce": "pp_toi",
        "shTimeOnIce": "sh_toi",
        "shifts": "shifts",
    },
    "realtime": {
        "hits": "hit",
        "blockedShots": "blk",
    },
    "faceoffpercentages": {
        "totalFaceoffs": "fo",
        "faceoffWinPct": "faceoff_win_pct",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_msk() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M МСК")


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def avg(values: list[float | int | None]) -> float | None:
    nums = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(nums) / len(nums) if nums else None


def delta_ratio(recent: float | None, baseline: float | None) -> tuple[float | None, float | None]:
    if recent is None or baseline is None:
        return None, None
    delta = recent - baseline
    if baseline <= 0:
        return delta, None
    return delta, recent / baseline


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def stable_without_meta(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: stable_without_meta(item)
            for key, item in value.items()
            if key not in {"updated", "generated_at", "checked_at"}
        }
    if isinstance(value, list):
        return [stable_without_meta(item) for item in value]
    return value


def write_if_changed(path: Path, payload: dict) -> bool:
    old = load_json(path)
    if old is not None and stable_without_meta(old) == stable_without_meta(payload):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return True


class NHLClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "DCCNHLFantasyRadar/1.0",
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.5",
        })

    def _get(self, url: str, *, params: dict | None = None) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = self.session.get(url, params=params, timeout=45)
                response.raise_for_status()
                return response
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"NHL request failed: {url}: {last_error}")

    def json(self, url: str, *, params: dict | None = None) -> dict:
        return self._get(url, params=params).json()

    def text(self, url: str) -> str:
        # The official media roster report is UTF-8 plain text, but its
        # Content-Type does not always provide a charset. Decode explicitly so
        # accented team/player names (e.g. MONTRÉAL) map correctly.
        return self._get(url).content.decode("utf-8-sig")


def fetch_report(client: NHLClient, report: str, from_date: str | None) -> list[dict]:
    expression = f"seasonId={NHL_SEASON_ID}"
    if from_date:
        expression += f" and gameDate>='{from_date}'"

    # The NHL Stats REST API documents limit=-1 as "return all results".
    # One complete response is safer than offset pagination here because the
    # report has no guaranteed default sort order and can change while pages
    # are being fetched.
    payload = client.json(
        f"{STATS_BASE}/{report}",
        params={
            "isAggregate": "false",
            "isGame": "true",
            "start": 0,
            "limit": -1,
            "cayenneExp": expression,
        },
    )

    rows = payload.get("data")
    if not isinstance(rows, list):
        raise RuntimeError(f"NHL stats report {report} returned no data array")

    total = payload.get("total")
    if total is not None and len(rows) != int(total):
        raise RuntimeError(
            f"NHL stats report {report} incomplete: got {len(rows)} of {total}"
        )

    keys = {
        (row.get("playerId"), row.get("gameId"))
        for row in rows
        if row.get("playerId") is not None and row.get("gameId") is not None
    }
    if len(keys) != len(rows):
        raise RuntimeError(
            f"NHL stats report {report} contains duplicate player/game rows"
        )

    return rows


def fetch_game_states(client: NHLClient, dates: set[str]) -> dict[int, dict]:
    states: dict[int, dict] = {}
    for game_date in sorted(dates):
        payload = client.json(f"{WEB_BASE}/score/{game_date}")
        games = payload.get("games")
        if not isinstance(games, list):
            raise RuntimeError(f"NHL score endpoint returned no games for {game_date}")
        for game in games:
            game_id = game.get("id")
            if game_id is None:
                continue
            state = game.get("gameState") or "UNKNOWN"
            states[int(game_id)] = {
                "state": state,
                "final": state in FINAL_STATES,
                "game_date": game.get("gameDate") or game_date,
            }
    return states


def combine_reports(report_rows: dict[str, list[dict]], states: dict[int, dict]) -> dict[int, dict]:
    games_by_player: dict[int, dict[int, dict]] = defaultdict(dict)

    for report, rows in report_rows.items():
        mapping = REPORT_FIELDS[report]
        for row in rows:
            player_id = row.get("playerId")
            game_id = row.get("gameId")
            if player_id is None or game_id is None:
                continue
            player_id = int(player_id)
            game_id = int(game_id)
            record = games_by_player[player_id].setdefault(game_id, {
                "game_id": game_id,
                "game_date": row.get("gameDate"),
                "name": row.get("skaterFullName"),
                "team": row.get("teamAbbrev"),
                "opponent": row.get("opponentTeamAbbrev"),
                "position": row.get("positionCode"),
            })
            for src, dst in mapping.items():
                if src in row:
                    record[dst] = row.get(src)
            for identity in ("gameDate", "skaterFullName", "teamAbbrev", "opponentTeamAbbrev", "positionCode"):
                if row.get(identity) is not None:
                    key = {
                        "gameDate": "game_date",
                        "skaterFullName": "name",
                        "teamAbbrev": "team",
                        "opponentTeamAbbrev": "opponent",
                        "positionCode": "position",
                    }[identity]
                    record[key] = row.get(identity)

    result: dict[int, dict] = {}
    for player_id, games in games_by_player.items():
        cleaned: list[dict] = []
        for game_id, record in games.items():
            state = states.get(game_id, {"state": "UNKNOWN", "final": False})
            record["game_state"] = state["state"]
            record["final"] = bool(state["final"])
            fo = record.get("fo")
            pct = record.get("faceoff_win_pct")
            record["fow"] = (
                int(round(float(fo) * float(pct)))
                if fo is not None and pct is not None
                else None
            )
            for key in (
                "g", "a", "pts", "plus_minus", "ppp", "shp", "sog",
                "toi", "ev_toi", "pp_toi", "sh_toi", "shifts", "hit", "blk",
                "fo", "fow",
            ):
                record.setdefault(key, None)
            cleaned.append(record)

        cleaned.sort(key=lambda x: ((x.get("game_date") or ""), int(x["game_id"])))
        latest = cleaned[-1] if cleaned else {}
        result[player_id] = {
            "nhl_player_id": player_id,
            "name": latest.get("name"),
            "team": latest.get("team"),
            "position": latest.get("position"),
            "games": cleaned,
        }

    return result


def merge_history(previous: dict | None, fresh_players: dict[int, dict]) -> dict:
    merged: dict[int, dict] = {}
    previous_players = (previous or {}).get("players", {})

    for player_id_str, player in previous_players.items():
        merged[int(player_id_str)] = {
            **player,
            "games": [dict(g) for g in player.get("games", [])],
        }

    for player_id, fresh in fresh_players.items():
        old_games = {
            int(g["game_id"]): dict(g)
            for g in merged.get(player_id, {}).get("games", [])
        }
        for game in fresh.get("games", []):
            old_games[int(game["game_id"])] = dict(game)
        games = sorted(
            old_games.values(),
            key=lambda x: ((x.get("game_date") or ""), int(x["game_id"])),
        )[-HISTORY_GAMES:]
        latest = games[-1] if games else {}
        merged[player_id] = {
            "nhl_player_id": player_id,
            "name": fresh.get("name") or merged.get(player_id, {}).get("name"),
            "team": latest.get("team") or fresh.get("team") or merged.get(player_id, {}).get("team"),
            "position": latest.get("position") or fresh.get("position") or merged.get(player_id, {}).get("position"),
            "games": games,
        }

    return {
        "schema_version": 1,
        "season_id": NHL_SEASON_ID,
        "updated": now_msk(),
        "source": {
            "stats": "https://api.nhle.com/stats/rest/en/",
            "game_status": "https://api-web.nhle.com/v1/score/{date}",
        },
        "history_games_per_player": HISTORY_GAMES,
        "players": {str(pid): merged[pid] for pid in sorted(merged)},
    }


def standings_name_map(client: NHLClient) -> dict[str, str]:
    payload = client.json(f"{WEB_BASE}/standings/now")
    mapping: dict[str, str] = {}
    for row in payload.get("standings", []):
        abbrev = (row.get("teamAbbrev") or {}).get("default")
        if not abbrev:
            continue
        names = [
            (row.get("teamName") or {}).get("default"),
            (row.get("teamCommonName") or {}).get("default"),
            (row.get("placeName") or {}).get("default"),
        ]
        for name in names:
            if name:
                mapping[normalize_name(name)] = abbrev
    return mapping


def parse_roster_report(text: str, team_map: dict[str, str]) -> dict[str, dict]:
    teams: dict[str, dict] = {}
    current_team: str | None = None
    section: str | None = None
    report_updated: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("Playing Roster - "):
            full_name = line.split("Playing Roster - ", 1)[1].strip()
            current_team = team_map.get(normalize_name(full_name))
            section = None
            if current_team:
                teams.setdefault(current_team, {
                    "name": full_name.title(),
                    "active": [],
                    "ir": [],
                })
            continue

        if line.startswith("Last update:") and report_updated is None:
            report_updated = line.split("Last update:", 1)[1].strip()
            continue

        if current_team is None:
            continue

        if line == "Active":
            section = "active"
            continue

        if line.startswith("Injury Reserve List"):
            section = "ir"
            continue

        # Column header belongs to the current section; do not clear it.
        if line.startswith("SW#"):
            continue

        if (
            line.startswith("Active -")
            or line.startswith("On IRL -")
            or line.startswith("Total -")
            or line.startswith("Average :")
            or line.startswith("Players by Country:")
        ):
            section = None
            continue

        if section not in {"active", "ir"}:
            continue

        columns = re.split(r"\s{2,}", line)
        if len(columns) < 3 or not columns[0].isdigit():
            continue

        sweater = int(columns[0])
        name = columns[1].strip()
        position = columns[2].strip()
        if position not in {"C", "L", "R", "D", "G"}:
            continue

        item = {
            "sweater": sweater,
            "name": name,
            "name_key": normalize_name(name),
            "position": position,
        }

        if section == "ir":
            possible_date = columns[-1].strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", possible_date):
                item["ir_date"] = possible_date

        teams[current_team][section].append(item)

    expected_teams = set(team_map.values())
    missing_teams = sorted(expected_teams - set(teams))
    if missing_teams:
        raise RuntimeError(
            "NHL roster report missing teams: " + ", ".join(missing_teams)
        )

    bad_active = {
        team: len(info.get("active", []))
        for team, info in teams.items()
        if len(info.get("active", [])) < 15
    }
    if bad_active:
        raise RuntimeError(
            f"NHL roster report active-roster completeness failed: {bad_active}"
        )

    return {
        "report_updated": report_updated,
        "teams": teams,
    }


def roster_name_display(history: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for player in history.get("players", {}).values():
        if player.get("name"):
            out[normalize_name(player["name"])] = player["name"]
    return out


def roster_snapshot_complete(snapshot: dict | None) -> bool:
    if not snapshot or not snapshot.get("complete"):
        return False
    teams = snapshot.get("teams", {})
    return (
        len(teams) >= 32
        and all(len(info.get("active", [])) >= 15 for info in teams.values())
    )


def build_roster_snapshot(parsed: dict, previous: dict | None, history: dict) -> dict:
    detected_at = now_iso()
    previous_valid = roster_snapshot_complete(previous)
    previous_teams = (previous or {}).get("teams", {}) if previous_valid else {}
    name_display = roster_name_display(history)
    new_events: list[dict] = []

    for team, current in parsed["teams"].items():
        old = previous_teams.get(team, {})
        old_active = {x["name_key"] for x in old.get("active", [])}
        old_ir = {x["name_key"] for x in old.get("ir", [])}
        cur_active = {x["name_key"] for x in current.get("active", [])}
        cur_ir = {x["name_key"] for x in current.get("ir", [])}

        def add_events(event_type: str, keys: set[str]) -> None:
            for key in sorted(keys):
                new_events.append({
                    "id": f"{event_type}:{team}:{key}:{detected_at[:10]}",
                    "type": event_type,
                    "team": team,
                    "player": name_display.get(key) or key.title(),
                    "player_key": key,
                    "detected_at": detected_at,
                })

        if previous_valid:
            add_events("NEW_IR", cur_ir - old_ir)
            add_events("RETURNED_FROM_IR", (old_ir - cur_ir) & cur_active)
            add_events("ACTIVE_ADDED", (cur_active - old_active) - old_ir)
            add_events("ACTIVE_REMOVED", (old_active - cur_active) - cur_ir)

    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_EVENT_DAYS)
    retained: list[dict] = []
    previous_events = (previous or {}).get("events", []) if previous_valid else []
    for event in previous_events:
        try:
            when = datetime.fromisoformat(event["detected_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        if when >= cutoff:
            retained.append(event)

    seen = {event["id"] for event in retained}
    for event in new_events:
        if event["id"] not in seen:
            retained.append(event)
            seen.add(event["id"])

    return {
        "schema_version": 1,
        "updated": now_msk(),
        "source": ROSTER_REPORT_URL,
        "report_updated": parsed.get("report_updated"),
        "complete": True,
        "team_count": len(parsed["teams"]),
        "active_count": sum(len(info.get("active", [])) for info in parsed["teams"].values()),
        "ir_count": sum(len(info.get("ir", [])) for info in parsed["teams"].values()),
        "teams": parsed["teams"],
        "events": sorted(retained, key=lambda x: x["detected_at"], reverse=True),
    }


def nhl_position_matches(nhl_position: str | None, espn: dict) -> bool:
    if not nhl_position:
        return True
    positions = set(espn.get("positions") or [])
    primary = espn.get("position")
    if primary:
        positions.add(primary)
    expected = {
        "C": {"C", "F"},
        "L": {"LW", "F"},
        "R": {"RW", "F"},
        "D": {"D"},
        "G": {"G"},
    }.get(nhl_position, {nhl_position})
    return bool(positions & expected)


def roster_team_by_name(roster: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for team, info in roster.get("teams", {}).items():
        for section in ("active", "ir"):
            for player in info.get(section, []):
                out[player["name_key"]] = team
    return out


def build_player_map(history: dict, roster: dict, analysis: dict, espn_index: dict) -> dict:
    espn_team_abbrev = {
        int(team["id"]): team.get("abbrev")
        for team in analysis.get("pro_teams", [])
        if team.get("id") is not None
    }
    espn_by_name: dict[str, list[dict]] = defaultdict(list)
    for player in espn_index.get("players", []):
        espn_by_name[normalize_name(player.get("name"))].append(player)

    current_nhl_team = roster_team_by_name(roster)
    mappings: list[dict] = []
    unmatched: list[dict] = []

    for nhl_id_str, nhl in history.get("players", {}).items():
        games = [g for g in nhl.get("games", []) if g.get("final")]
        if not games or nhl.get("position") == "G":
            continue
        name = nhl.get("name")
        key = normalize_name(name)
        candidates = espn_by_name.get(key, [])
        if not candidates:
            unmatched.append({"nhl_player_id": int(nhl_id_str), "name": name, "reason": "no_exact_name"})
            continue

        expected_team = current_nhl_team.get(key) or nhl.get("team")
        filtered = []
        for candidate in candidates:
            espn_team = espn_team_abbrev.get(int(candidate.get("pro_team_id") or 0))
            team_ok = not expected_team or not espn_team or espn_team == expected_team
            position_ok = nhl_position_matches(nhl.get("position"), candidate)
            if team_ok and position_ok:
                filtered.append(candidate)

        if len(filtered) != 1:
            unmatched.append({
                "nhl_player_id": int(nhl_id_str),
                "name": name,
                "reason": "ambiguous_or_team_position_mismatch",
                "candidate_count": len(candidates),
                "filtered_count": len(filtered),
            })
            continue

        espn = filtered[0]
        mappings.append({
            "nhl_player_id": int(nhl_id_str),
            "espn_player_id": int(espn["id"]),
            "name": name,
            "nhl_team": expected_team,
            "position": nhl.get("position"),
            "espn_status": espn.get("availability"),
            "espn_team_id": espn.get("on_team_id"),
            "espn_pro_team_id": espn.get("pro_team_id"),
            "percent_owned": espn.get("percent_owned"),
            "method": "exact_normalized_full_name+team+position",
            "confidence": "HIGH",
        })

    return {
        "schema_version": 1,
        "updated": now_msk(),
        "method": "Exact normalized full name with NHL/ESPN team and position validation; no surname-only fuzzy joins.",
        "mapped": mappings,
        "unmatched": unmatched,
        "coverage": {
            "mapped": len(mappings),
            "unmatched": len(unmatched),
            "ratio": round(len(mappings) / max(1, len(mappings) + len(unmatched)), 4),
        },
    }


def metric_window(games: list[dict]) -> tuple[list[dict], list[dict]]:
    final_games = [g for g in games if g.get("final")]
    if len(final_games) >= 5:
        recent = final_games[-2:]
        baseline = final_games[max(0, len(final_games) - 7):-2]
        if len(baseline) >= 3:
            return baseline[-5:], recent
    if len(final_games) == 4:
        return final_games[:3], final_games[-1:]
    return [], []


def mean_metric(games: list[dict], key: str) -> float | None:
    return avg([g.get(key) for g in games])


def round_metric(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def roster_status_for(name_key: str, team: str | None, roster: dict) -> str:
    if not team:
        return "UNKNOWN"
    info = roster.get("teams", {}).get(team, {})
    if any(x["name_key"] == name_key for x in info.get("ir", [])):
        return "IR"
    if any(x["name_key"] == name_key for x in info.get("active", [])):
        return "ACTIVE"
    return "UNKNOWN"


def recent_team_events(team: str | None, roster: dict) -> list[dict]:
    if not team:
        return []
    return [event for event in roster.get("events", []) if event.get("team") == team][:6]


def detect_player_signal(player: dict, mapping: dict | None, roster: dict) -> dict | None:
    baseline, recent = metric_window(player.get("games", []))
    if not baseline or not recent:
        return None

    metrics: dict[str, dict] = {}
    for key in ("toi", "ev_toi", "pp_toi", "sh_toi", "sog", "hit", "blk", "fow", "fo", "pts", "ppp", "shp"):
        b = mean_metric(baseline, key)
        r = mean_metric(recent, key)
        d, ratio = delta_ratio(r, b)
        metrics[key] = {
            "baseline": round_metric(b),
            "recent": round_metric(r),
            "delta": round_metric(d),
            "ratio": round_metric(ratio, 3),
        }

    signals: list[dict] = []
    score = 0

    toi = metrics["toi"]
    toi_strong = False
    if (
        toi["delta"] is not None
        and toi["ratio"] is not None
        and toi["delta"] >= 180
        and toi["ratio"] >= 1.15
    ):
        toi_strong = toi["delta"] >= 300 or toi["ratio"] >= 1.25
        score += 45 if toi_strong else 30
        signals.append({
            "type": "TOI_SURGE",
            "label": "Рост TOI",
            "strong": toi_strong,
        })

    pp = metrics["pp_toi"]
    if (
        pp["recent"] is not None
        and pp["delta"] is not None
        and pp["recent"] >= 60
        and pp["delta"] >= 60
    ):
        score += 25
        signals.append({"type": "PP_ROLE_UP", "label": "Рост PP TOI", "strong": pp["recent"] >= 120})

    sh = metrics["sh_toi"]
    if (
        sh["recent"] is not None
        and sh["delta"] is not None
        and sh["recent"] >= 90
        and sh["delta"] >= 60
    ):
        score += 18
        signals.append({"type": "SH_ROLE_UP", "label": "Рост SH TOI", "strong": sh["recent"] >= 150})

    baseline_peripheral = sum(x or 0 for x in (
        metrics["sog"]["baseline"], metrics["hit"]["baseline"], metrics["blk"]["baseline"]
    ))
    recent_peripheral = sum(x or 0 for x in (
        metrics["sog"]["recent"], metrics["hit"]["recent"], metrics["blk"]["recent"]
    ))
    if baseline_peripheral > 0 and recent_peripheral >= baseline_peripheral + 1.5 and recent_peripheral / baseline_peripheral >= 1.25:
        score += 15
        signals.append({"type": "PERIPHERAL_UP", "label": "Рост SOG/HIT/BLK", "strong": recent_peripheral >= baseline_peripheral + 3})

    fow = metrics["fow"]
    if (
        fow["recent"] is not None
        and fow["delta"] is not None
        and fow["recent"] >= 5
        and fow["delta"] >= 3
    ):
        score += 12
        signals.append({"type": "FOW_UP", "label": "Рост FOW", "strong": fow["recent"] >= 8})

    pts = metrics["pts"]
    if (
        pts["recent"] is not None
        and pts["baseline"] is not None
        and pts["recent"] >= 1
        and pts["recent"] >= pts["baseline"] + 0.5
    ):
        score += 10
        signals.append({"type": "PRODUCTION_UP", "label": "Рост результативности", "strong": pts["recent"] >= 1.5})

    if sum((g.get("shp") or 0) for g in recent) >= 1:
        score += 8
        signals.append({"type": "SHP_EVENT", "label": "SHP в последних матчах", "strong": False})

    if sum((g.get("ppp") or 0) for g in recent) >= 1 and (metrics["ppp"]["recent"] or 0) > (metrics["ppp"]["baseline"] or 0):
        score += 6
        signals.append({"type": "PPP_UP", "label": "Рост PPP", "strong": False})

    if not signals:
        return None

    if len(recent) == 2 and toi_strong:
        elevated = [
            (g.get("toi") or 0) >= (metrics["toi"]["baseline"] or 0) + 120
            for g in recent
        ]
        if all(elevated):
            score += 10

    score = min(100, score)
    reliability = "LOW" if len(recent) == 1 else ("HIGH" if len(baseline) >= 5 else "MEDIUM")

    name_key = normalize_name(player.get("name"))
    team = player.get("team")
    nhl_roster_status = roster_status_for(name_key, team, roster)
    events = recent_team_events(team, roster)

    availability = mapping.get("espn_status") if mapping else "UNMATCHED"
    recommendation = "TRACK ONLY"
    if availability in AVAILABLE_STATUSES:
        # Every available player that clears at least one radar trigger is a
        # WATCH by default. ADD CANDIDATE is reserved for stronger, repeated
        # signals; players owned by another DCC team remain TRACK ONLY.
        recommendation = "WATCH"
        if nhl_roster_status != "IR" and reliability != "LOW" and (
            score >= 65
            or (
                toi_strong
                and (metrics["toi"]["recent"] or 0) >= 1080
                and len(recent) >= 2
            )
        ):
            recommendation = "ADD CANDIDATE"
    elif availability == "UNMATCHED":
        recommendation = "WATCH"

    categories: list[str] = []
    if any(x["type"] == "TOI_SURGE" for x in signals):
        categories.append("TOI")
    if any(x["type"] == "PP_ROLE_UP" for x in signals):
        categories.extend(["PPP"])
    if any(x["type"] in {"SH_ROLE_UP", "SHP_EVENT"} for x in signals):
        categories.extend(["SHP"])
    if (metrics["sog"]["recent"] or 0) >= 2:
        categories.append("SOG")
    if (metrics["hit"]["recent"] or 0) >= 2:
        categories.append("HIT")
    if (metrics["blk"]["recent"] or 0) >= 2:
        categories.append("BLK")
    if (metrics["fow"]["recent"] or 0) >= 5:
        categories.append("FOW")
    if (metrics["pts"]["recent"] or 0) >= 0.75:
        categories.extend(["G", "A", "PTS"])
    categories = list(dict.fromkeys(categories))

    if len(recent) >= 2 and toi_strong:
        role_outlook = "Повышенный TOI держится минимум два завершённых матча подряд."
    elif len(recent) >= 2:
        role_outlook = "Изменение видно в двух последних завершённых матчах; устойчивость роли ещё нужно подтверждать."
    else:
        role_outlook = "Сигнал пока основан на одном завершённом матче; нужна повторная проверка."

    latest_game = [g for g in player.get("games", []) if g.get("final")][-1]

    return {
        "nhl_player_id": int(player["nhl_player_id"]),
        "espn_player_id": mapping.get("espn_player_id") if mapping else None,
        "name": player.get("name"),
        "nhl_team": team,
        "position": player.get("position"),
        "espn_status": availability,
        "espn_team_id": mapping.get("espn_team_id") if mapping else None,
        "percent_owned": mapping.get("percent_owned") if mapping else None,
        "nhl_roster_status": nhl_roster_status,
        "score": score,
        "reliability": reliability,
        "recommendation": recommendation,
        "categories": categories,
        "signals": signals,
        "metrics": metrics,
        "baseline_games": len(baseline),
        "recent_games": len(recent),
        "role_outlook": role_outlook,
        "team_context": events,
        "latest_final_game_date": latest_game.get("game_date"),
        "latest_final_game_id": latest_game.get("game_id"),
    }


def build_radar(history: dict, roster: dict, mapping: dict, previous: dict | None) -> dict:
    map_by_nhl = {
        int(row["nhl_player_id"]): row
        for row in mapping.get("mapped", [])
    }
    signals: list[dict] = []
    for nhl_id_str, player in history.get("players", {}).items():
        row = detect_player_signal(player, map_by_nhl.get(int(nhl_id_str)), roster)
        if row:
            signals.append(row)

    signals.sort(key=lambda x: (
        x["recommendation"] != "ADD CANDIDATE",
        -int(x["score"]),
        x.get("name") or "",
    ))

    previous_ids = set()
    if previous:
        for row in previous.get("signals", []):
            for signal in row.get("signals", []):
                previous_ids.add(f"{row['nhl_player_id']}:{signal['type']}")

    new_signals: list[dict] = []
    if previous:
        for row in signals:
            new_types = [
                signal["type"]
                for signal in row.get("signals", [])
                if f"{row['nhl_player_id']}:{signal['type']}" not in previous_ids
            ]
            if new_types:
                new_signals.append({
                    "nhl_player_id": row["nhl_player_id"],
                    "name": row["name"],
                    "nhl_team": row["nhl_team"],
                    "espn_status": row["espn_status"],
                    "recommendation": row["recommendation"],
                    "score": row["score"],
                    "new_signal_types": new_types,
                })

    final_dates = [
        g.get("game_date")
        for player in history.get("players", {}).values()
        for g in player.get("games", [])
        if g.get("final") and g.get("game_date")
    ]
    available = [s for s in signals if s.get("espn_status") in AVAILABLE_STATUSES]

    return {
        "schema_version": 1,
        "updated": now_msk(),
        "generated_at": now_iso(),
        "source": {
            "nhl_stats": "https://api.nhle.com/stats/rest/en/",
            "nhl_game_status": "https://api-web.nhle.com/v1/",
            "nhl_roster_ir": ROSTER_REPORT_URL,
            "espn_availability": "analysis.json + players/index.json",
        },
        "complete": True,
        "latest_final_game_date": max(final_dates) if final_dates else None,
        "mapping_coverage": mapping.get("coverage"),
        "signal_method": {
            "baseline": "Previous 3-5 finalized games",
            "recent": "Latest 2 finalized games; one-game early WATCH is allowed after 3-game baseline",
            "note": "Thresholds are screening triggers, not proof of a permanent role change. Line combinations are not inferred from NHL API TOI.",
        },
        "counts": {
            "signals_all": len(signals),
            "signals_available": len(available),
            "add_candidates": sum(1 for x in available if x["recommendation"] == "ADD CANDIDATE"),
            "watch": sum(1 for x in available if x["recommendation"] == "WATCH"),
            "new_signals": len(new_signals),
        },
        "new_signals": new_signals[:30],
        "signals": signals,
        "recent_roster_events": roster.get("events", [])[:50],
    }


def validate_wilsby(history: dict, mapping: dict, radar: dict, roster: dict) -> dict:
    player = next(
        (p for p in history.get("players", {}).values() if normalize_name(p.get("name")) == "adam wilsby"),
        None,
    )
    if not player:
        raise RuntimeError("Wilsby validation failed: player not found in NHL history")
    games = [g for g in player.get("games", []) if g.get("final")]
    by_date = {g.get("game_date"): g for g in games}
    required = ["2026-10-01", "2026-10-03", "2026-10-06", "2026-10-08", "2026-10-10", "2026-10-17"]
    missing = [d for d in required if d not in by_date]
    if missing:
        # This is a rolling 12-game history. The October regression fixtures
        # naturally age out; do not block live NHL updates when they do.
        return {
            "nhl_player_id": int(player["nhl_player_id"]),
            "validation": "SKIPPED_HISTORY_WINDOW",
            "missing_dates": missing,
            "roster_status": roster_status_for("adam wilsby", "NSH", roster),
        }

    first3 = [by_date[d] for d in required[:3]]
    next2 = [by_date[d] for d in required[3:5]]
    baseline_toi = avg([g.get("toi") for g in first3])
    recent_toi = avg([g.get("toi") for g in next2])
    if baseline_toi is None or recent_toi is None or recent_toi - baseline_toi < 300:
        raise RuntimeError("Wilsby validation failed: expected >5 minute TOI jump not detected")

    mapped = next((x for x in mapping.get("mapped", []) if x.get("name") == "Adam Wilsby"), None)
    if not mapped:
        raise RuntimeError("Wilsby validation failed: ESPN mapping missing")

    wilsby_roster_status = roster_status_for("adam wilsby", "NSH", roster)
    if wilsby_roster_status != "ACTIVE":
        raise RuntimeError(
            f"Wilsby validation failed: official roster status is {wilsby_roster_status}"
        )

    # Wilsby is a historical regression control: the collector must preserve
    # the Oct 1-10 usage jump even after the rolling *current* radar window has
    # moved on to later games. Do not require him to emit TOI_SURGE forever.
    radar_row = next(
        (x for x in radar.get("signals", []) if x.get("name") == "Adam Wilsby"),
        None,
    )

    return {
        "nhl_player_id": int(player["nhl_player_id"]),
        "espn_player_id": mapped["espn_player_id"],
        "baseline_toi_seconds": round(baseline_toi, 1),
        "oct8_10_toi_seconds": round(recent_toi, 1),
        "delta_seconds": round(recent_toi - baseline_toi, 1),
        "radar_recommendation_now": radar_row["recommendation"] if radar_row else None,
        "roster_status": wilsby_roster_status,
    }


def run(*, dry_run: bool, strict: bool) -> int:
    try:
        client = NHLClient()
        previous_history = load_json(HISTORY_PATH)
        previous_roster = load_json(ROSTER_PATH)
        previous_radar = load_json(RADAR_PATH)

        if previous_history and previous_history.get("players"):
            from_day = (date.today() - timedelta(days=REFRESH_DAYS)).isoformat()
        else:
            from_day = None

        reports = {
            report: fetch_report(client, report, from_day)
            for report in REPORT_FIELDS
        }
        all_dates = {
            row.get("gameDate")
            for rows in reports.values()
            for row in rows
            if row.get("gameDate")
        }
        states = fetch_game_states(client, all_dates)
        fresh_players = combine_reports(reports, states)
        history = merge_history(previous_history, fresh_players)

        team_map = standings_name_map(client)
        roster_text = client.text(ROSTER_REPORT_URL)
        parsed_roster = parse_roster_report(roster_text, team_map)
        roster = build_roster_snapshot(parsed_roster, previous_roster, history)

        analysis = load_json(ROOT / "analysis.json")
        espn_index = load_json(ROOT / "players" / "index.json")
        if not analysis or not espn_index:
            raise RuntimeError("ESPN analysis.json / players/index.json is missing")
        mapping = build_player_map(history, roster, analysis, espn_index)
        radar = build_radar(history, roster, mapping, previous_radar)

        wilsby = validate_wilsby(history, mapping, radar, roster)

        print(
            "NHL radar validation: "
            f"players={len(history.get('players', {}))}, "
            f"mapped={mapping['coverage']['mapped']}, "
            f"signals={radar['counts']['signals_all']}, "
            f"available={radar['counts']['signals_available']}"
        )
        if wilsby.get("validation") == "SKIPPED_HISTORY_WINDOW":
            print(
                "Wilsby historical control skipped (rolling history aged out): "
                + ", ".join(wilsby["missing_dates"])
            )
        else:
            print(
                "Wilsby control: "
                f"baseline={wilsby['baseline_toi_seconds']}s, "
                f"Oct8-10={wilsby['oct8_10_toi_seconds']}s, "
                f"delta={wilsby['delta_seconds']}s, "
                f"ESPN={wilsby['espn_player_id']}, "
                f"roster={wilsby['roster_status']}"
            )

        if dry_run:
            return 0

        changed = []
        for path, payload in (
            (HISTORY_PATH, history),
            (ROSTER_PATH, roster),
            (MAP_PATH, mapping),
            (RADAR_PATH, radar),
        ):
            if write_if_changed(path, payload):
                changed.append(str(path.relative_to(ROOT)))

        if changed:
            print("NHL radar files updated: " + ", ".join(changed))
        else:
            print("NHL radar checked: no meaningful changes.")
        return 0

    except Exception as exc:
        print(f"::warning::NHL Fantasy Radar refresh failed; previous published data preserved: {exc}")
        if strict:
            raise
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate but do not write files")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on NHL API/validation error")
    args = parser.parse_args()
    raise SystemExit(run(dry_run=args.dry_run, strict=args.strict))


if __name__ == "__main__":
    main()
