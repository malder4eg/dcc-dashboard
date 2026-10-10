"""Public, allowlisted ESPN snapshots for roster and waiver analysis.

Only hockey data is exported. Members, cookies, messages, bids and contact
details never enter these files. Player detail files are split for easy retrieval.
"""
from __future__ import annotations

import json
from pathlib import Path

SLOTS = {0: "C", 1: "LW", 2: "RW", 3: "F", 4: "D", 5: "G",
         6: "UTIL", 7: "BN", 8: "IR"}
POSITIONS = {1: "C", 2: "LW", 3: "RW", 4: "D", 5: "G"}
PAGE_SIZE = 500
CHUNK_SIZE = 100


def pick(data, keys):
    return {key: data[key] for key in keys if key in data}


def normalize_player(entry, categories):
    pool = entry.get("playerPoolEntry", entry)
    player = pool.get("player") or {}
    player_id = player.get("id", pool.get("id"))
    if player_id is None or not player.get("fullName"):
        raise RuntimeError("ESPN returned a player without an ID or name")
    splits = []
    for item in player.get("stats", []):
        raw = item.get("stats")
        if not isinstance(raw, dict):
            continue
        split = pick(item, ("id", "seasonId", "scoringPeriodId", "statSourceId",
                            "statSplitTypeId", "externalId"))
        split["raw"] = raw
        split["categories"] = {cat["key"]: raw.get(cat["stat"]) for cat in categories}
        splits.append(split)
    return {
        "id": int(player_id), "name": player["fullName"],
        "pro_team_id": player.get("proTeamId"),
        "position": POSITIONS.get(player.get("defaultPositionId")),
        "eligible_slot_ids": player.get("eligibleSlots", []),
        "positions": [SLOTS[s] for s in player.get("eligibleSlots", []) if s in range(6)],
        "injury_status": player.get("injuryStatus"),
        "injured": player.get("injured"),
        "availability": pool.get("status"),
        "on_team_id": pool.get("onTeamId"),
        "waiver_process_at": pool.get("waiverProcessDate"),
        "ownership": pick(player.get("ownership") or {}, ("percentOwned", "percentStarted", "percentChange")),
        "stats": splits,
    }


def fetch_available(client, get_json, scoring_period, season, categories):
    """Read the entire FA/waiver pool, detecting ignored pagination."""
    result = {}
    for offset in range(0, 20000, PAGE_SIZE):
        filters = {"players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
            "limit": PAGE_SIZE, "offset": offset,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            "filterStatsForExternalIds": {"value": [season, season - 1]},
            "filterStatsForSourceIds": {"value": [0, 1]},
            "filterStatsForSplitTypeIds": {"value": [0, 1, 2, 3]},
        }}
        data = get_json(client, params=[("view", "kona_player_info"),
                                       ("scoringPeriodId", str(scoring_period))],
                        headers={"x-fantasy-filter": json.dumps(filters)})
        if "players" not in data:
            raise RuntimeError("ESPN did not return the FA/waiver pool")
        batch = data["players"]
        new = 0
        for entry in batch:
            player = normalize_player(entry, categories)
            if player["availability"] not in {"FREEAGENT", "WAIVERS", None}:
                raise RuntimeError("ESPN ignored the FA/waiver status filter")
            if player["id"] not in result:
                result[player["id"]] = player
                new += 1
        print(f"FA/waivers: offset={offset}, received={len(batch)}, new={new}")
        if batch and new == 0:
            raise RuntimeError("ESPN ignored pagination; refusing an incomplete player pool")
        if len(batch) < PAGE_SIZE:
            # ESPN can cap pages below our requested limit. If it reports a
            # total, don't claim completeness until that total is reached.
            total = data.get("filterStats", {}).get("players", {}).get("totalCount")
            if total is not None and len(result) < int(total):
                raise RuntimeError("ESPN truncated the available-player pool")
            return list(result.values())
    raise RuntimeError("ESPN player pool exceeds the safety page limit")


def build_snapshot(base, roster_data, available, *, categories, season, scoring_period, current_week, updated):
    names = {int(t["id"]): t for t in base.get("teams", [])}
    roster_teams = {int(t["id"]): t for t in roster_data.get("teams", [])}
    if names.keys() != roster_teams.keys():
        raise RuntimeError("Roster response does not include every league team")
    teams, players, roster_ids = [], {}, set()
    for team_id, team in names.items():
        roster = roster_teams[team_id].get("roster", {})
        if "entries" not in roster:
            raise RuntimeError(f"Missing roster for team {team_id}")
        entries = []
        for entry in roster["entries"]:
            player = normalize_player(entry, categories)
            if player["id"] in roster_ids:
                raise RuntimeError("Player appears on multiple rosters")
            roster_ids.add(player["id"])
            player["availability"], player["on_team_id"] = "ONTEAM", team_id
            players[player["id"]] = player
            slot_id = entry.get("lineupSlotId")
            entries.append({"player_id": player["id"], "slot_id": slot_id,
                            "slot": SLOTS.get(slot_id, str(slot_id)),
                            "acquisition_type": entry.get("acquisitionType"),
                            "acquired_at": entry.get("acquisitionDate")})
        name = team.get("name") or " ".join(filter(None, (team.get("location"), team.get("nickname")))) or team.get("abbrev")
        teams.append({"id": team_id, "name": name, "roster": entries,
                      "record": pick(team.get("record", {}).get("overall", {}), ("wins", "losses", "ties", "percentage")),
                      "transaction_counter": pick(roster_teams[team_id].get("transactionCounter") or team.get("transactionCounter") or {},
                                                  ("acquisitions", "drops", "trades", "acquisitionBudgetSpent", "matchupAcquisitions")),
                      "waiver_rank": team.get("waiverRank")})
    overlap = roster_ids & {p["id"] for p in available}
    if overlap:
        raise RuntimeError("Roster and FA pool disagree; league may have changed during refresh")
    players.update({p["id"]: p for p in available})
    settings = base.get("settings", {})
    pro_teams = settings.get("proTeams", [])
    snapshot = {
        "schema_version": 1, "updated": updated, "season_id": season,
        "scoring_period_id": scoring_period, "current_week": current_week,
        "complete": True, "teams": teams,
        "available_count": len(available), "rostered_count": len(roster_ids),
        "settings": pick(settings, ("acquisitionSettings", "rosterSettings", "scoringSettings", "scheduleSettings", "tradeSettings")),
        "pro_teams": [pick(t, ("id", "abbrev", "name", "location")) for t in pro_teams],
        "matchups": [{"week": m.get("matchupPeriodId"), "home_team_id": (m.get("home") or {}).get("teamId"),
                      "away_team_id": (m.get("away") or {}).get("teamId")} for m in base.get("schedule", [])],
        "notes": ["Snapshot, not a live ESPN session.",
                  "Transaction counters are ESPN fields; acquisitions must not be assumed to mean weekly ADD.",
                  "Missing fields mean unknown, not zero. Stats split/source IDs distinguish actuals from projections.",
                  "Lines, PP/PK roles and confirmed starters require separate current sources."],
    }
    # Files remain small enough to read independently through GitHub tools.
    chunks, index = {}, []
    for i, player in enumerate(sorted(players.values(), key=lambda p: p["id"])):
        filename = f"players/{i // CHUNK_SIZE:03d}.json"
        chunks.setdefault(filename, []).append(player)
        row = {key: value for key, value in player.items() if key not in {"stats", "ownership"}}
        row["detail_file"] = filename
        row["percent_owned"] = player["ownership"].get("percentOwned")
        index.append(row)
    snapshot["player_index_file"] = "players/index.json"
    files = {"analysis.json": snapshot, "players/index.json": {"updated": updated, "complete": True, "players": index}}
    files.update({name: {"updated": updated, "players": items} for name, items in chunks.items()})
    return files


def export_league(client, get_json, base, categories, season, scoring_period, current_week, updated):
    roster_data = get_json(client, params=[("view", "mRoster"), ("view", "mTeam"),
                                          ("scoringPeriodId", str(scoring_period))])
    # Pro schedules also supply current NHL abbreviations, avoiding a hardcoded
    # franchise map (relocations and ESPN team IDs can change).
    pro_data = get_json(client, params=[("view", "proTeamSchedules_wl")],
                        url=f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fhl/seasons/{season}")
    pro_teams = pro_data.get("settings", {}).get("proTeams", [])
    if not pro_teams:
        raise RuntimeError("ESPN did not return NHL teams/schedules")
    base = dict(base)
    base["settings"] = dict(base.get("settings", {}), proTeams=pro_teams)
    available = fetch_available(client, get_json, scoring_period, season, categories)
    files = build_snapshot(base, roster_data, available, categories=categories, season=season,
                           scoring_period=scoring_period, current_week=current_week, updated=updated)
    games = {}
    for team in pro_teams:
        for period, items in team.get("proGamesByScoringPeriod", {}).items():
            for game in items:
                clean = pick(game, ("id", "date", "homeProTeamId", "awayProTeamId", "status"))
                clean["scoring_period_id"] = int(period)
                key = str(game.get("id") or (period, game.get("homeProTeamId"), game.get("awayProTeamId")))
                games[key] = clean
    files["nhl_schedule.json"] = {"updated": updated, "season_id": season, "games": list(games.values())}
    files["analysis.json"]["nhl_schedule_file"] = "nhl_schedule.json"
    return files


def write_snapshot(root: Path, files: dict):
    for filename, content in files.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(content, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        temp.replace(path)
    for old in (root / "players").glob("*.json"):
        if f"players/{old.name}" not in files:
            old.unlink()
