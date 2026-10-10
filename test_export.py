import json
import unittest
from unittest.mock import patch

from league_export import build_snapshot, fetch_available
from scrape import CATEGORIES


def player(pid, status="ONTEAM"):
    return {"status": status, "player": {"id": pid, "fullName": f"Player {pid}",
            "defaultPositionId": 4, "proTeamId": 25, "eligibleSlots": [4, 6, 7, 8],
            "injuryStatus": "OUT", "injured": True,
            "stats": [{"id": "002027", "seasonId": 2027, "statSourceId": 0,
                       "statSplitTypeId": 0, "stats": {"26": 3600, "32": 7, "13": 0}}]}}


class SnapshotTests(unittest.TestCase):
    def make(self, available=None, roster=None):
        base = {"teams": [{"id": 12, "name": "Mighty Ducks", "owners": ["PRIVATE-ID"]}],
                "members": [{"email": "PRIVATE-EMAIL"}],
                "settings": {"name": "DCC", "acquisitionSettings": {"matchupAcquisitionLimit": 7}},
                "schedule": [{"matchupPeriodId": 2, "home": {"teamId": 12}, "away": {"teamId": 1}}]}
        roster = roster or {"teams": [{"id": 12, "roster": {"entries": [
            {"lineupSlotId": 8, "playerPoolEntry": player(1)}]}}]}
        return build_snapshot(base, roster, available or [], categories=CATEGORIES,
                              season=2027, scoring_period=9, current_week=2, updated="now")

    def test_roster_ir_missing_vs_zero_and_private_fields(self):
        files = self.make()
        self.assertEqual(files["analysis.json"]["teams"][0]["roster"][0]["slot"], "IR")
        p = files["players/000.json"]["players"][0]
        self.assertEqual(p["stats"][0]["categories"]["TOI"], 3600)
        self.assertEqual(p["stats"][0]["categories"]["G"], 0)
        self.assertIsNone(p["stats"][0]["categories"]["A"])
        self.assertEqual(p["positions"], ["D"])
        self.assertNotIn("PRIVATE", json.dumps(files))

    def test_missing_roster_aborts(self):
        with self.assertRaises(RuntimeError):
            self.make(roster={"teams": []})

    def test_roster_fa_conflict_aborts(self):
        from league_export import normalize_player
        with self.assertRaises(RuntimeError):
            self.make(available=[normalize_player(player(1, "FREEAGENT"), CATEGORIES)])

    def test_pagination_and_waivers(self):
        pages = iter([[player(2, "FREEAGENT"), player(3, "WAIVERS")], [player(4, "FREEAGENT")]])
        def get(*args, **kwargs):
            return {"players": next(pages)}
        with patch("league_export.PAGE_SIZE", 2):
            pool = fetch_available(None, get, 9, 2027, CATEGORIES)
        self.assertEqual([p["id"] for p in pool], [2, 3, 4])
        self.assertEqual(pool[1]["availability"], "WAIVERS")

    def test_repeated_page_aborts(self):
        def get(*args, **kwargs):
            return {"players": [player(2, "FREEAGENT"), player(3, "WAIVERS")]}
        with patch("league_export.PAGE_SIZE", 2), self.assertRaises(RuntimeError):
            fetch_available(None, get, 9, 2027, CATEGORIES)


if __name__ == "__main__":
    unittest.main()
