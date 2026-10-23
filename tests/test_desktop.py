"""Desktop-app mode of the review screen, and the self-test, on any OS."""

import re

import pytest
from fastapi.testclient import TestClient

from scrubber.desktop import app as desktop
from scrubber.ui.app import create_app


class Hooks:
    def __init__(self, paths, input_dir, lock_ok):
        self._paths, self._input, self._lock = paths, input_dir, lock_ok

    def paths(self):
        return self._paths

    def settings(self):
        return desktop.Settings(input=str(self._input), output=str(self._paths.output))

    def lock(self):
        return (True, "On") if self._lock else (False, "The internet block for this app is missing.")


def _client(workspace, tmp_path, lock_ok=True, chosen=True):
    hooks = Hooks(workspace, workspace.input if chosen else "", lock_ok)
    return TestClient(create_app(None, str(tmp_path / "config" / "scrubber.yaml"), desktop=hooks),
                      base_url="http://127.0.0.1:5123")


def _csrf(client):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get("/").text).group(1)


def test_home_shows_folders_lock_and_start(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    page = _client(workspace, tmp_path).get("/").text
    assert "Scrub your data" in page and str(workspace.input) in page
    assert 'data-app="choose_input"' in page and "Start scrubbing" in page
    assert '<script src="/static/desktop.js"' in page


def test_start_refused_without_network_lock(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    client = _client(workspace, tmp_path, lock_ok=False)
    assert "disabled" in client.get("/").text.split("Start scrubbing")[0][-120:]
    client.post("/rerun", data={"csrf_token": _csrf(client)})
    assert not (workspace.work / "requests" / "rerun.json").exists()


def test_start_needs_a_folder(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    client = _client(workspace, tmp_path, chosen=False)
    r = client.post("/rerun", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert "Choose%20the%20folder" in r.headers["location"]


def test_start_requests_a_scrub(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    client = _client(workspace, tmp_path)
    client.post("/rerun", data={"csrf_token": _csrf(client)})
    assert (workspace.work / "requests" / "rerun.json").exists()


def test_desktop_script_served(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    r = _client(workspace, tmp_path).get("/static/desktop.js")
    assert r.status_code == 200 and "pywebviewready" in r.text


def test_lock_outside_windows(monkeypatch):
    """The Mac trial build runs without a firewall rule; Linux has no desktop app."""
    monkeypatch.delenv("SCRUBBER_DEV_ALLOW_NO_LOCK", raising=False)
    if desktop.os.name != "nt":
        assert desktop.network_lock()[0] is (desktop.sys.platform == "darwin")


def test_self_test_passes(monkeypatch, capsys):
    from scrubber.desktop import selftest
    monkeypatch.setenv("SCRUBBER_START_METHOD", "spawn")
    assert selftest.run() == 0
    assert '"leaks": []' in capsys.readouterr().out


@pytest.mark.parametrize("method", ["spawn"])
def test_pipeline_with_spawned_workers(workspace, synth, run_pipeline, monkeypatch, method):
    """Windows cannot fork: workers start fresh and rebuild the scrubber."""
    monkeypatch.setenv("SCRUBBER_START_METHOD", method)
    people = [synth.person() for _ in range(4)]
    for i, p in enumerate(people):
        (workspace.input / f"note{i}.txt").write_text(synth.note(p))
    runner, summary = run_pipeline(workspace, workers=2)
    assert summary["files"]["done"] == 4
    out = "".join(f.read_text() for f in workspace.released.rglob("*.txt"))
    for p in people:
        assert p.last not in out and p.ssn not in out


def test_drop_a_folder_uses_it_directly(tmp_path):
    folder = tmp_path / "Exports"
    folder.mkdir()
    assert desktop.dropped_input([str(folder)]) == folder


def test_drop_files_copies_them_to_a_private_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "app_dir", lambda: tmp_path / "app")
    a, b = tmp_path / "a.csv", tmp_path / "notes.txt"
    a.write_text("x")
    b.write_text("y")
    target = desktop.dropped_input([str(a), str(b)])
    assert target == tmp_path / "app" / "dropped" and sorted(p.name for p in target.iterdir()) == ["a.csv", "notes.txt"]
    # a second drop replaces the first copy
    c = tmp_path / "other.eml"
    c.write_text("z")
    target = desktop.dropped_input([str(c)])
    assert [p.name for p in target.iterdir()] == ["other.eml"]

