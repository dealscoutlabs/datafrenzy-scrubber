"""Desktop-app mode of the review screen, and the self-test, on any OS."""

import re

import pytest
from fastapi.testclient import TestClient

from scrubber.desktop import app as desktop
from scrubber.ui.app import create_app


class Hooks:
    def __init__(self, paths, input_dir, lock_ok, mode="simple"):
        self._paths, self._input, self._lock, self._mode = paths, input_dir, lock_ok, mode

    def paths(self):
        return self._paths

    def settings(self):
        return desktop.Settings()

    def lock(self):
        return (True, "On") if self._lock else (False, "The internet block for this app is missing.")

    def mode(self):
        return self._mode

    def set_mode(self, mode):
        self._mode = mode

    def batch(self):
        return {"files": 0, "exported": 0, "review": 0, "not_exported": 0, "records": 0}

    def history(self):
        return [{"started": 1767225600, "files": 3, "exported": 2, "review": 1, "not_exported": 0, "records": 10,
                 "saved_to": ["/Users/me/Desktop/Data Frenzy Scrubbed 2026-01-01 09.00"]}]


def _client(workspace, tmp_path, lock_ok=True, chosen=True, mode="simple"):
    if not chosen:
        for f in workspace.input.iterdir():
            f.unlink()
    hooks = Hooks(workspace, workspace.input, lock_ok, mode)
    return TestClient(create_app(None, str(tmp_path / "config" / "scrubber.yaml"), desktop=hooks),
                      base_url="http://127.0.0.1:5123")


def _csrf(client):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get("/").text).group(1)


def test_home_asks_for_files(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    page = _client(workspace, tmp_path).get("/").text
    assert "Scrub your files" in page and 'data-app="add_files"' in page and 'data-app="add_folder"' in page
    assert '<script src="/static/desktop.js"' in page
    assert 'href="/samples"' not in page  # simple mode hides the detailed screens


def test_detailed_mode_shows_review_screens(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    client = _client(workspace, tmp_path)
    token = _csrf(client)
    client.post("/mode", data={"csrf_token": token, "mode": "detailed"})
    page = client.get("/").text
    assert 'href="/samples"' in page and 'href="/history"' in page
    assert "Data Frenzy Scrubbed 2026-01-01" in client.get("/history").text


def test_batch_lists_files_and_offers_save(workspace, synth, run_pipeline, tmp_path):
    (tmp_path / "config").mkdir()
    (workspace.input / "note.txt").write_text(synth.note(synth.person()))
    run_pipeline(workspace)
    client = _client(workspace, tmp_path)
    page = client.get("/").text
    assert "note.txt" in page and 'data-app="save_output"' in page and 'data-app="new_batch"' in page


def test_start_refused_without_network_lock(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    client = _client(workspace, tmp_path, lock_ok=False)
    assert "disabled" in client.get("/").text.split('data-app="add_files"')[1][:20]
    client.post("/rerun", data={"csrf_token": _csrf(client)})
    assert not (workspace.work / "requests" / "rerun.json").exists()


def test_start_needs_files(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    client = _client(workspace, tmp_path, chosen=False)
    r = client.post("/rerun", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert "Add%20files" in r.headers["location"]


def test_start_requests_a_scrub(workspace, tmp_path):
    (tmp_path / "config").mkdir()
    (workspace.input / "note.txt").write_text("hello")
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


def test_batch_add_save_and_new(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "app_dir", lambda: tmp_path / "app")
    (tmp_path / "app").mkdir()
    folder = tmp_path / "Exports"
    folder.mkdir()
    (folder / "a.csv").write_text("x")
    loose = tmp_path / "notes.txt"
    loose.write_text("y")
    assert desktop.add_to_batch([str(folder), str(loose), str(loose)]) == 3
    inbox = desktop.current_paths().input
    assert sorted(p.name for p in inbox.iterdir()) == ["Exports", "notes (2).txt", "notes.txt"]
    assert (desktop.current_paths().work / "requests" / "rerun.json").exists()
    assert loose.read_text() == "y"  # originals untouched

    out = desktop.current_paths().output / "scrubbed"
    out.mkdir(parents=True)
    (out / "notes.txt").write_text("clean")
    saved = desktop.save_output(str(tmp_path / "Desktop"))
    assert (saved / "scrubbed" / "notes.txt").read_text() == "clean" and saved.name.startswith("Data Frenzy Scrubbed")

    desktop.new_batch()
    assert not desktop.batch_dir().exists()
    (entry,) = desktop.history()
    assert entry["saved_to"] == [str(saved)] and "notes" not in str({k: v for k, v in entry.items() if k != "saved_to"})


def test_text_clippings_are_not_added(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "app_dir", lambda: tmp_path / "app")
    (tmp_path / "app").mkdir()
    clip = tmp_path / "Discharge Report.textClipping"
    clip.write_bytes(b"bplist00")
    assert desktop.add_to_batch([str(clip)]) == 0
