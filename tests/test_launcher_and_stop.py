"""Starting the app without a command line (shortcut, single copy, free
port) and stopping it from inside the app."""

import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from chess_mistake_coach.db import open_db, save_game, set_settings
from chess_mistake_coach.web import serve_cli
from chess_mistake_coach.web.app import create_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = ("127.0.0.1", 50000)


def _app(tmp_path, server=True, job=None, imp=None):
    db = str(tmp_path / "s.db")
    conn = open_db(db)
    set_settings(conn, primary_user="alice")
    save_game(conn, {"url": "g1", "username": "alice", "end_time": 1, "date": "2026-09-01",
                     "time_class": "blitz", "my_colour": "white", "my_rating": 1500,
                     "opp_rating": 1500, "result": "win", "eco": "C20", "moves_played": 30,
                     "opening_moves": 10, "middlegame_moves": 15, "endgame_moves": 5}, [], 14)
    conn.close()
    app = create_app(db)
    if server:
        app.state.server = SimpleNamespace(should_exit=False)
    app.state.jobs = MagicMock()
    app.state.jobs.get_active_job_id.return_value = job
    app.state.puzzle_import = MagicMock()
    app.state.puzzle_import.get_active_job_id.return_value = imp
    return app


def _wait_for_exit(server, seconds=2.0):
    end = time.time() + seconds
    while time.time() < end and not server.should_exit:
        time.sleep(0.05)
    return server.should_exit


# ---- stopping ---------------------------------------------------------------------

def test_health_identifies_the_app(tmp_path):
    assert TestClient(_app(tmp_path)).get("/api/health").json() == {"app": "chess-mistake-coach"}


def test_stop_from_this_computer_stops_the_server(tmp_path):
    app = _app(tmp_path)
    r = TestClient(app, client=LOCAL).post("/api/shutdown", json={})
    assert r.json() == {"stopping": True}
    assert _wait_for_exit(app.state.server)


def test_stop_is_refused_from_another_device(tmp_path):
    app = _app(tmp_path)
    r = TestClient(app, client=("192.168.1.20", 50000)).post("/api/shutdown", json={})
    assert r.status_code == 403
    time.sleep(0.4)
    assert app.state.server.should_exit is False


def test_stop_asks_first_while_an_update_is_running(tmp_path):
    app = _app(tmp_path, job="job-1")
    client = TestClient(app, client=LOCAL)
    assert client.post("/api/shutdown", json={}).json() == {"stopping": False, "busy": "update"}
    app.state.jobs.cancel.assert_not_called()
    assert client.post("/api/shutdown", json={"force": True}).json() == {"stopping": True}
    app.state.jobs.cancel.assert_called_once_with("job-1")
    assert _wait_for_exit(app.state.server)


def test_stop_cancels_a_puzzle_import_too(tmp_path):
    app = _app(tmp_path, imp="imp-1")
    client = TestClient(app, client=LOCAL)
    assert client.post("/api/shutdown", json={}).json()["busy"] == "puzzle import"
    client.post("/api/shutdown", json={"force": True})
    app.state.puzzle_import.cancel.assert_called_once_with("imp-1")


def test_stop_without_the_apps_own_server_says_so(tmp_path):
    r = TestClient(_app(tmp_path, server=False), client=LOCAL).post("/api/shutdown", json={})
    assert r.status_code == 503


def test_pages_carry_the_stop_button(tmp_path):
    client = TestClient(_app(tmp_path))
    for url in ("/", "/settings", "/dashboard"):
        html = client.get(url).text
        assert 'id="stopAppBtn"' in html and "/api/shutdown" in html, url


def test_the_browser_tab_icon_is_served(tmp_path):
    r = TestClient(_app(tmp_path)).get("/favicon.ico")
    assert r.status_code == 200 and r.content[:4] == b"\x00\x00\x01\x00"


# ---- starting ------------------------------------------------------------------------

def test_reuses_an_already_running_copy(monkeypatch):
    monkeypatch.setattr(serve_cli, "running_here", lambda port, timeout=1.0: port == 8000)
    monkeypatch.setattr(serve_cli, "port_free", lambda host, port: False)
    assert serve_cli.choose_port("127.0.0.1", None) == (8000, True)


def test_skips_a_port_used_by_something_else(monkeypatch):
    monkeypatch.setattr(serve_cli, "running_here", lambda port, timeout=1.0: False)
    monkeypatch.setattr(serve_cli, "port_free", lambda host, port: port == 8002)
    assert serve_cli.choose_port("127.0.0.1", None) == (8002, False)


def test_an_explicit_port_is_the_only_one_tried(monkeypatch):
    monkeypatch.setattr(serve_cli, "running_here", lambda port, timeout=1.0: False)
    monkeypatch.setattr(serve_cli, "port_free", lambda host, port: port != 9000)
    assert serve_cli.choose_port("127.0.0.1", 9000) == (None, False)


def test_already_running_just_opens_a_window(monkeypatch):
    opened = []
    monkeypatch.setattr(serve_cli, "choose_port", lambda host, port: (8000, True))
    monkeypatch.setattr(serve_cli, "_open_window", opened.append)
    monkeypatch.setattr(serve_cli, "_log_to_file_if_no_console", lambda db: None)
    serve_cli.serve_main([])
    assert opened == ["http://127.0.0.1:8000/"]


def test_nothing_free_reports_it(monkeypatch):
    monkeypatch.setattr(serve_cli, "choose_port", lambda host, port: (None, False))
    monkeypatch.setattr(serve_cli, "_log_to_file_if_no_console", lambda db: None)
    with pytest.raises(SystemExit) as exc:
        serve_cli.serve_main(["--no-browser"])
    assert "free port" in str(exc.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows message box")
def test_a_failure_only_pops_a_message_box_when_there_is_no_console(monkeypatch):
    import ctypes
    calls = []
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda *a: calls.append(a) or 1)
    monkeypatch.setattr(serve_cli, "_no_console", False)
    with pytest.raises(SystemExit):
        serve_cli._fail("boom")
    assert calls == []
    monkeypatch.setattr(serve_cli, "_no_console", True)
    with pytest.raises(SystemExit):
        serve_cli._fail("boom")
    assert len(calls) == 1


def test_running_here_is_false_when_nothing_answers():
    assert serve_cli.running_here(1, timeout=0.2) is False


def test_the_app_can_be_started_as_a_module_for_shortcuts():
    assert os.path.isfile(os.path.join(ROOT, "chess_mistake_coach", "__main__.py"))
    assert os.path.isfile(os.path.join(ROOT, "chess_mistake_coach", "web", "static", "icon.ico"))
    assert os.path.isfile(os.path.join(ROOT, "chess_mistake_coach", "web", "static", "icon.png"))


def test_installers_create_launchers_that_start_without_a_console():
    ps1 = open(os.path.join(ROOT, "install.ps1"), encoding="utf-8").read()
    assert "pythonw.exe" in ps1 and "CreateShortcut" in ps1 and "-m chess_mistake_coach serve" in ps1
    sh = open(os.path.join(ROOT, "install.sh"), encoding="utf-8").read()
    assert "osacompile" in sh and ".desktop" in sh and "-m chess_mistake_coach serve" in sh
