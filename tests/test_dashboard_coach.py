"""The dashboard improvements: real time-pressure rates (moves counted per
clock band), openings per 100 moves, training links, and the page itself."""

import inspect
import json
import re

import chess
import chess.engine
import chess.pgn
from fastapi.testclient import TestClient

from chess_mistake_coach import analysis, backup, coaching
from chess_mistake_coach.analysis import (CLOCK_BUCKET_LABELS, analyse_game, backfill_clock_moves,
                                          clock_bucket_index, count_clock_moves)
from chess_mistake_coach.db import open_db, save_game, set_settings
from chess_mistake_coach.html_export import DASHBOARD_ECO_MIN_GAMES, build_dashboard_data
from chess_mistake_coach.web.app import create_app

USER = "alice"


def _pgn(moves_uci, clocks, white=USER, black="bob"):
    """A Chess.com-style game JSON whose moves carry %clk comments."""
    game = chess.pgn.Game()
    game.headers.update({"White": white, "Black": black, "ECO": "C20"})
    node = game
    for uci, clk in zip(moves_uci, clocks):
        node = node.add_variation(chess.Move.from_uci(uci))
        if clk is not None:
            node.set_clock(clk)
    return {"url": "https://x/g1", "pgn": str(game), "end_time": 1_700_000_000, "time_class": "blitz",
            "white": {"username": white, "rating": 1500, "result": "win"},
            "black": {"username": black, "rating": 1500, "result": "lose"}}


MOVES = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "d2d3", "f8c5"]
#        white  black  white  black  white  black  white  black
CLOCKS = [100.0, 99.0, 45.0, 90.0, 12.0, 80.0, None, 70.0]


# ---- clock bands --------------------------------------------------------------

def test_clock_bucket_index_matches_the_labelled_bands():
    assert [clock_bucket_index(s) for s in (0, 29.9, 30, 59.9, 60, 600)] == [0, 0, 1, 1, 2, 2]
    assert len(CLOCK_BUCKET_LABELS) == 3


def test_count_clock_moves_counts_only_your_clocked_moves_by_phase_and_band():
    counts = count_clock_moves(_pgn(MOVES, CLOCKS), USER)
    # White's moves: 100s (over 60), 45s (30-60), 12s (under 30), one with no clock.
    assert counts["opening"] == [1, 1, 1]
    assert counts["middlegame"] == [0, 0, 0] and counts["endgame"] == [0, 0, 0]
    black = count_clock_moves(_pgn(MOVES, CLOCKS, white="bob", black=USER), USER)
    assert black["opening"] == [0, 0, 4]


def test_count_clock_moves_handles_missing_pgn_and_other_players():
    assert count_clock_moves({}, USER) is None
    assert count_clock_moves(_pgn(MOVES, CLOCKS, white="x", black="y"), USER) is None


class _Engine:
    """Every move is the engine's best: no mistakes, two moves of analysis each."""

    def analyse(self, board, limit, **kw):
        move = sorted(board.legal_moves, key=lambda m: m.uci())[0]
        return {"score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE), "pv": [move]}


def test_analyse_game_records_the_same_clock_counts():
    rec, _ = analyse_game(_pgn(MOVES, CLOCKS), USER, _Engine(), 8, 50)
    assert rec["clock_moves"] == count_clock_moves(_pgn(MOVES, CLOCKS), USER)


def _rec(url, clock_moves=None, eco="C20", colour="white", moves=30, om=10, mm=15, em=5, tc="blitz"):
    r = {"url": url, "username": USER, "end_time": 1, "date": "2026-09-01", "time_class": tc,
         "my_colour": colour, "my_rating": 1500, "opp_rating": 1500, "result": "win", "eco": eco,
         "moves_played": moves, "opening_moves": om, "middlegame_moves": mm, "endgame_moves": em}
    if clock_moves is not None:
        r["clock_moves"] = clock_moves
    return r


def _mistake(url, clock, phase="middlegame", category="hung a pawn", severity="mistake"):
    return {"game_url": url, "username": USER, "date": "2026-09-01", "end_time": 1, "time_class": "blitz",
            "my_rating": 1500, "my_colour": "white", "move_number": 15, "phase": phase,
            "severity": severity, "cp_loss": 150, "category": category, "played": "e4", "best": "d4",
            "clock_seconds": clock, "fen": "f"}


def test_save_game_stores_clock_counts_and_replaces_them_on_reanalysis():
    conn = open_db(":memory:")
    save_game(conn, _rec("g1", {"opening": [1, 2, 3], "middlegame": [0, 0, 0], "endgame": [4, 0, 0]}), [], 14)
    rows = {r["phase"]: tuple(r)[3:] for r in conn.execute("SELECT * FROM game_clock_moves")}
    assert rows == {"opening": (1, 2, 3), "middlegame": (0, 0, 0), "endgame": (4, 0, 0)}
    save_game(conn, _rec("g1", {"opening": [9, 9, 9], "middlegame": [0, 0, 0], "endgame": [0, 0, 0]}), [], 20)
    assert conn.execute("SELECT under_30 FROM game_clock_moves WHERE phase='opening'").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM game_clock_moves").fetchone()[0] == 3


def test_a_game_saved_without_clock_counts_writes_none():
    conn = open_db(":memory:")
    save_game(conn, _rec("g1"), [], 14)
    assert conn.execute("SELECT COUNT(*) FROM game_clock_moves").fetchone()[0] == 0


def test_backfill_counts_older_games_from_the_stored_downloads_once():
    conn = open_db(":memory:")
    g = _pgn(MOVES, CLOCKS)
    save_game(conn, _rec(g["url"]), [], 14)               # analysed before clock counts existed
    save_game(conn, _rec("https://x/gone"), [], 14)       # its download is no longer stored
    conn.execute("INSERT INTO archives (url, username, month, body, game_count, complete) "
                 "VALUES ('a', ?, '2026-09', ?, 1, 1)", (USER, json.dumps([g])))
    conn.commit()
    assert backfill_clock_moves(conn, USER) == 1
    row = conn.execute("SELECT under_30, s30_to_60, over_60 FROM game_clock_moves "
                       "WHERE game_url = ? AND phase = 'opening'", (g["url"],)).fetchone()
    assert tuple(row) == (1, 1, 1)
    assert backfill_clock_moves(conn, USER) == 0          # nothing left to do
    assert conn.execute("SELECT COUNT(*) FROM game_clock_moves WHERE game_url = 'https://x/gone'").fetchone()[0] == 0


# ---- what the dashboard is given -------------------------------------------------

def _sum(table, **want):
    out = 0
    for row in table["data"]:
        if all(row[table["dims"].index(k)] == v for k, v in want.items()):
            out += row[len(table["dims"])]
    return out


def test_time_pressure_rates_use_moves_made_in_each_band():
    conn = open_db(":memory:")
    # Counted game: 10 moves under 30s, 90 with over a minute; 2 errors in each band.
    save_game(conn, _rec("g1", {"opening": [0, 0, 0], "middlegame": [10, 0, 90], "endgame": [0, 0, 0]}),
              [_mistake("g1", 10.0), _mistake("g1", 20.0), _mistake("g1", 100.0), _mistake("g1", 200.0)], 14)
    # Uncounted game: its errors must not leak into the numerator.
    save_game(conn, _rec("g2"), [_mistake("g2", 5.0)], 14)
    data = build_dashboard_data(conn, [USER])
    moves, errs = data["clockMovesFacts"], data["clockBucketFacts"]
    under = _sum(errs, clockBucket=0) / _sum(moves, clockBucket=0) * 100
    over = _sum(errs, clockBucket=2) / _sum(moves, clockBucket=2) * 100
    assert round(under, 1) == 20.0 and round(over, 1) == 2.2
    assert data["meta"]["clockCoverage"][USER] == {"counted": 1, "total": 2}


def test_openings_are_rated_per_100_moves_with_the_phase_on_both_sides():
    conn = open_db(":memory:")
    for i in range(12):
        save_game(conn, _rec(f"g{i}", eco="B02", colour="black", om=10, mm=20, em=0),
                  [_mistake(f"g{i}", 50.0, phase="opening")], 14)
    data = build_dashboard_data(conn, [USER])
    assert data["meta"]["ecoMinGames"] == DASHBOARD_ECO_MIN_GAMES == 10
    eco = data["lists"]["ecos"].index("B02")
    opening = data["lists"]["phases"].index("opening")
    assert _sum(data["ecoMovesFacts"], eco=eco) == 12 * 30
    assert _sum(data["ecoMovesFacts"], eco=eco, phase=opening) == 12 * 10


def test_every_mistake_type_with_puzzle_themes_is_a_real_category():
    categories = set(re.findall(r'return "([^"]+)"', inspect.getsource(analysis.classify)))
    assert set(coaching.CATEGORY_PUZZLE_THEMES) <= categories
    # Only planning has no fitting puzzles.
    assert categories - set(coaching.CATEGORY_PUZZLE_THEMES) == {"positional or planning error"}
    data = build_dashboard_data(open_db(":memory:"), [USER])
    assert data["meta"]["trainingThemes"] == coaching.CATEGORY_PUZZLE_THEMES


def test_clock_counts_travel_with_a_backup():
    assert "game_clock_moves" in backup.USER_TABLES


# ---- the page --------------------------------------------------------------------------

def _app(tmp_path, primary=True):
    db = str(tmp_path / "d.db")
    conn = open_db(db)
    save_game(conn, _rec("g1", {"opening": [1, 1, 1], "middlegame": [1, 1, 1], "endgame": [0, 0, 0]}),
              [_mistake("g1", 10.0)], 14)
    if primary:
        set_settings(conn, primary_user=USER)
    conn.close()
    return TestClient(create_app(db))


def test_dashboard_defaults_to_your_own_player(tmp_path):
    r = _app(tmp_path).get("/dashboard")
    assert r.status_code == 200 and "<h1>Dashboard</h1>" in r.text and '"alice"' in r.text


def test_dashboard_without_a_player_or_a_primary_user_still_asks(tmp_path):
    assert _app(tmp_path, primary=False).get("/dashboard").status_code == 400


def test_dashboard_page_has_the_new_layout(tmp_path):
    html = _app(tmp_path).get("/dashboard").text
    for hook in ('id="glance"', 'id="typeList"', 'id="clockBars"', 'class="fchip"', 'id="resetFilters"',
                 'aria-current="page">Dashboard', "clockMovesFacts", "trainingThemes"):
        assert hook in html, hook
    assert "Chess Mistake Explorer" not in html


def test_other_pages_link_to_the_dashboard(tmp_path):
    html = _app(tmp_path).get("/practice").text
    assert '<a href="/dashboard"' in html
