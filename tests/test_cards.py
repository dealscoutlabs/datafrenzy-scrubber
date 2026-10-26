"""Card review: one card per flagged item across the batch; Good / PII apply everywhere."""

import re

import pytest
from fastapi.testclient import TestClient

from scrubber.config import config_hash, load_config
from scrubber.ui.app import create_app

PHRASE = "our only pediatric surgeon"     # a context-risk flag the double-check always raises


@pytest.fixture
def batch(workspace, run_pipeline, synth, tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "scrubber.yaml"
    for name in ("a.txt", "b.txt", "c.txt"):
        (workspace.input / name).write_text(f"Seen by {PHRASE} today.\n")
    (workspace.input / "d.txt").write_text("Referred to the only pediatric cardiologist in the county.\n")
    runner, _ = run_pipeline(workspace, overrides=None)
    client = TestClient(create_app(workspace, str(cfg_path)), base_url="http://localhost:8080")
    return client, runner, cfg_path


def _csrf(client):
    return re.search(r'name="csrf_token" id="csrf" value="([^"]+)"', client.get("/cards").text).group(1)


def _decide(client, token, card, decision, who="Pat"):
    r = client.post("/cards/decide", data={"csrf_token": token, "card": card, "decision": decision, "reviewer": who})
    assert r.status_code == 200, r.text


def _rerun(workspace, run_pipeline, cfg_path):
    # what the app does after "Finish": run again with the updated lists
    from scrubber.pipeline import Runner
    runner = Runner(workspace, load_config(str(cfg_path), {"reference_year": 2026}), workers=1, echo=lambda *_: None)
    runner.run()
    return runner


def test_one_card_per_item_across_files(batch):
    client, runner, _ = batch
    deck = client.get("/cards/deck.json").json()
    shared = next(c for c in deck if c["value"] and c["value"].lower() == PHRASE)
    assert shared["count"] == 3 and shared["kind"] == "item"
    assert deck[0]["count"] == 3                              # the most widespread comes first
    assert "⟦" in shared["snippet"] and shared["what"] == "a detail that could point to one person"
    page = client.get("/cards").text
    assert 'class="back" href="/"' in page and "/static/cards.js" in page


def test_good_releases_files_and_is_remembered(batch, workspace, run_pipeline):
    client, runner, cfg_path = batch
    token = _csrf(client)
    for c in client.get("/cards/deck.json").json():
        _decide(client, token, c["id"], "good")
    assert client.get("/cards/deck.json").json() == []
    r = client.post("/cards/finish", data={"csrf_token": token, "reviewer": "Pat"}, follow_redirects=False)
    assert r.status_code == 303 and "Ready" in r.headers["location"]
    assert {f["rel"]: f["status"] for f in runner.store.files()} == {
        "a.txt": "done", "b.txt": "done", "c.txt": "done", "d.txt": "done"}
    assert (workspace.released / "a.txt").exists()
    assert not (workspace.work / "requests" / "rerun.json").exists()   # Good alone needs no re-scrub
    approved = runner.store.reviews("approved")
    assert approved and all(rv["decided_by"] == "Pat" for rv in approved)
    # remembered: the same text in a later file isn't flagged, and no re-scrub of everything was triggered
    cfg = load_config(str(cfg_path))
    assert any(e["text"].lower() == PHRASE and e["by"] == "Pat" for e in cfg["known_fine"])
    assert config_hash(cfg) == config_hash({k: v for k, v in cfg.items() if k != "known_fine"})
    (workspace.input / "e.txt").write_text(f"Also seen by {PHRASE.upper()}.\n")
    runner = _rerun(workspace, run_pipeline, cfg_path)
    assert runner.store.file("e.txt")["status"] == "done"
    assert PHRASE in client.get("/known-fine").text.lower()


def test_pii_blacks_it_out_everywhere(batch, workspace, run_pipeline):
    client, runner, cfg_path = batch
    token = _csrf(client)
    deck = client.get("/cards/deck.json").json()
    shared = next(c for c in deck if c["count"] == 3)
    _decide(client, token, shared["id"], "pii")
    for c in deck:
        if c is not shared:
            _decide(client, token, c["id"], "good")
    r = client.post("/cards/finish", data={"csrf_token": token, "reviewer": "Pat"}, follow_redirects=False)
    assert "blacked out" in r.headers["location"].replace("%20", " ")
    assert (workspace.work / "requests" / "rerun.json").exists()
    assert runner.store.file("d.txt")["status"] == "done"          # its only card was Good
    runner = _rerun(workspace, run_pipeline, cfg_path)
    for name in ("a.txt", "b.txt", "c.txt"):
        assert runner.store.file(name)["status"] == "done"
        assert PHRASE not in (workspace.released / name).read_text().lower()


def test_undo_takes_a_choice_back(batch):
    client, runner, _ = batch
    token = _csrf(client)
    deck = client.get("/cards/deck.json").json()
    _decide(client, token, deck[0]["id"], "good")
    assert len(client.get("/cards/deck.json").json()) == len(deck) - 1
    assert client.post("/cards/undo", data={"csrf_token": token, "card": deck[0]["id"]}).json() == {"ok": True}
    assert len(client.get("/cards/deck.json").json()) == len(deck)


def test_choices_need_a_name_and_a_token(batch):
    client, _, _ = batch
    token = _csrf(client)
    card = client.get("/cards/deck.json").json()[0]["id"]
    assert client.post("/cards/decide", data={"csrf_token": token, "card": card, "decision": "good",
                                              "reviewer": ""}).status_code == 400
    assert client.post("/cards/decide", data={"csrf_token": "x", "card": card, "decision": "good",
                                              "reviewer": "Pat"}).status_code == 403


def test_undecided_files_stay_held(batch):
    client, runner, _ = batch
    token = _csrf(client)
    shared = next(c for c in client.get("/cards/deck.json").json() if c["count"] == 3)
    _decide(client, token, shared["id"], "good")
    client.post("/cards/finish", data={"csrf_token": token, "reviewer": "Pat"})
    assert runner.store.file("a.txt")["status"] == "done" and runner.store.file("d.txt")["status"] == "review"


def test_pdf_card_shows_a_crop(workspace, run_pipeline, tmp_path):
    import pymupdf
    (tmp_path / "config").mkdir()
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_text((72, 100), "Quarterly wage report", fontsize=14)
    pg.insert_text((72, 140), f"Prepared for {PHRASE} of the clinic.", fontsize=11)
    doc.save(workspace.input / "report.pdf")
    runner, _ = run_pipeline(workspace)
    client = TestClient(create_app(workspace, str(tmp_path / "config" / "scrubber.yaml")),
                        base_url="http://localhost:8080")
    card = next(c for c in client.get("/cards/deck.json").json() if c["kind"] == "item")
    assert card["crop"] is True
    r = client.get(f"/cards/crop/{card['id']}.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert client.get("/cards/crop/zzz.png").status_code == 404
