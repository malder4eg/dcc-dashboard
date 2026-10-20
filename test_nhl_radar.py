import unittest

from nhl_radar import (
    build_player_map,
    detect_player_signal,
    metric_window,
)


def game(day, toi, *, final=True, pp=0, sh=0, sog=0, hit=0, blk=0, pts=0, ppp=0, shp=0, fow=0, fo=0):
    return {
        "game_id": int(day.replace("-", "")),
        "game_date": f"2026-{day}",
        "name": "Adam Wilsby",
        "team": "NSH",
        "position": "D",
        "game_state": "OFF" if final else "LIVE",
        "final": final,
        "toi": toi,
        "ev_toi": max(0, toi - pp - sh),
        "pp_toi": pp,
        "sh_toi": sh,
        "sog": sog,
        "hit": hit,
        "blk": blk,
        "pts": pts,
        "ppp": ppp,
        "shp": shp,
        "fow": fow,
        "fo": fo,
        "g": 0,
        "a": pts,
        "plus_minus": 0,
        "shifts": 20,
    }


class RadarTests(unittest.TestCase):
    def wilsby_player(self):
        return {
            "nhl_player_id": 8482482,
            "name": "Adam Wilsby",
            "team": "NSH",
            "position": "D",
            "games": [
                game("10-01", 965, sh=120, blk=3),
                game("10-03", 873, sh=35, sog=1, blk=2),
                game("10-06", 905, sh=132, sog=2, hit=2, pts=1),
                game("10-08", 1241, sh=137, hit=1, blk=2, pts=1),
                game("10-10", 1224, sh=60, sog=2, hit=1, blk=1),
            ],
        }

    def test_wilsby_toi_surge_detected_after_two_elevated_games(self):
        player = self.wilsby_player()
        mapping = {
            "espn_player_id": 4697765,
            "espn_status": "FREEAGENT",
            "espn_team_id": 0,
            "percent_owned": 0.32,
        }
        row = detect_player_signal(player, mapping, {"teams": {}, "events": []})
        self.assertIsNotNone(row)
        self.assertTrue(any(s["type"] == "TOI_SURGE" for s in row["signals"]))
        self.assertEqual(row["recommendation"], "ADD CANDIDATE")
        self.assertGreaterEqual(row["metrics"]["toi"]["delta"], 300)

    def test_live_game_is_excluded_from_signal_window(self):
        player = self.wilsby_player()
        player["games"].append(game("10-11", 3000, final=False, pp=900))
        baseline, recent = metric_window(player["games"])
        self.assertEqual(len(baseline), 3)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[-1]["game_date"], "2026-10-10")

    def test_exact_name_team_position_mapping(self):
        history = {"players": {
            "8482482": {
                "nhl_player_id": 8482482,
                "name": "Adam Wilsby",
                "team": "NSH",
                "position": "D",
                "games": [game("10-10", 1224)],
            }
        }}
        roster = {"teams": {"NSH": {
            "active": [{"name_key": "adam wilsby"}],
            "ir": [],
        }}}
        analysis = {"pro_teams": [{"id": 27, "abbrev": "NSH"}]}
        espn = {"players": [{
            "id": 4697765,
            "name": "Adam Wilsby",
            "pro_team_id": 27,
            "position": "D",
            "positions": ["D"],
            "availability": "FREEAGENT",
            "on_team_id": 0,
            "percent_owned": 0.32,
        }]}
        result = build_player_map(history, roster, analysis, espn)
        self.assertEqual(result["coverage"]["mapped"], 1)
        self.assertEqual(result["mapped"][0]["espn_player_id"], 4697765)

    def test_no_surname_only_fuzzy_mapping(self):
        history = {"players": {
            "8482482": {
                "nhl_player_id": 8482482,
                "name": "Adam Wilsby",
                "team": "NSH",
                "position": "D",
                "games": [game("10-10", 1224)],
            }
        }}
        roster = {"teams": {"NSH": {"active": [], "ir": []}}}
        analysis = {"pro_teams": [{"id": 27, "abbrev": "NSH"}]}
        espn = {"players": [{
            "id": 1,
            "name": "Adam X Wilsby",
            "pro_team_id": 27,
            "position": "D",
            "positions": ["D"],
            "availability": "FREEAGENT",
            "on_team_id": 0,
        }]}
        result = build_player_map(history, roster, analysis, espn)
        self.assertEqual(result["coverage"]["mapped"], 0)
        self.assertEqual(result["unmatched"][0]["reason"], "no_exact_name")

    def test_ir_caps_available_player_at_watch(self):
        player = self.wilsby_player()
        mapping = {
            "espn_player_id": 4697765,
            "espn_status": "FREEAGENT",
            "espn_team_id": 0,
            "percent_owned": 0.32,
        }
        roster = {"teams": {"NSH": {
            "active": [],
            "ir": [{"name_key": "adam wilsby"}],
        }}, "events": []}
        row = detect_player_signal(player, mapping, roster)
        self.assertEqual(row["nhl_roster_status"], "IR")
        self.assertEqual(row["recommendation"], "WATCH")


if __name__ == "__main__":
    unittest.main()
