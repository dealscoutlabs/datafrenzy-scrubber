import re

import pytest
import yaml
from fastapi.testclient import TestClient

from fixtures import synth_images as si
from scrubber.config import load_config
from scrubber.ui.app import create_app, file_id


@pytest.fixture
def ui(workspace, run_pipeline, synth, tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    p = synth.person()
    (workspace.input / "note.txt").write_text(f"Patient {p.name} asked our only pediatric surgeon. Call {p.phone}.\n")
    (workspace.input / "letter.txt").write_text(f"Dear {p.first}, your appointment on 3/14/2024 is confirmed.\n")
    (workspace.input / "badge.png").write_bytes(si.save(si.id_badge(p), "PNG"))
    runner, _ = run_pipeline(workspace)
    client = TestClient(create_app(workspace, str(cfg_dir / "scrubber.yaml")), base_url="http://localhost:8080")
    return client, runner, cfg_dir, p


def _csrf(client, url="/"):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_home_and_counts(ui):
    client, runner, _, _ = ui
    r = client.get("/")
    assert r.status_code == 200 and "files exported" in r.text and "waiting for your review" in r.text
    assert "Telephone numbers" in r.text
    assert r.headers["cache-control"] == "no-store" and "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_only_localhost(ui):
    client, *_ = ui
    assert client.get("/", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/", headers={"host": "127.0.0.1:8080"}).status_code == 200
    token = _csrf(client)
    r = client.post("/rerun", data={"csrf_token": token}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_forms_need_csrf_token(ui, workspace):
    client, *_ = ui
    assert client.post("/rerun", data={"csrf_token": "wrong"}).status_code == 403
    assert not (workspace.work / "requests" / "rerun.json").exists()


def test_review_queue_and_approve(ui, workspace):
    client, runner, _, p = ui
    (rv,) = runner.store.reviews("pending")
    fid = file_id(rv["rel"])
    page = client.get(f"/review/{fid}").text
    assert "<mark>our only pediatric surgeon</mark>" in page
    assert p.name in page  # the original sample is shown locally
    token = _csrf(client, f"/review/{fid}")
    assert client.post(f"/review/{fid}/approve", data={"csrf_token": token, "reviewer": ""}).status_code == 400
    r = client.post(f"/review/{fid}/approve", data={"csrf_token": token, "reviewer": "Pat", "note": "ok"},
                    follow_redirects=False)
    assert r.status_code == 303 and "reviewer=Pat" in r.headers["set-cookie"]
    assert (workspace.released / "note.txt").exists()
    assert "Pat" in (workspace.reports / "deidentification-report.html").read_text()
    assert runner.store.reviews("approved")


def test_reject_keeps_file_out(ui, workspace):
    client, runner, _, _ = ui
    (rv,) = runner.store.reviews("pending")
    fid = file_id(rv["rel"])
    client.post(f"/review/{fid}/reject", data={"csrf_token": _csrf(client), "reviewer": "Pat"})
    assert not (workspace.released / "note.txt").exists()
    assert runner.store.file(rv["rel"])["status"] == "rejected"


def test_samples_and_report_a_miss(ui):
    client, runner, cfg_dir, p = ui
    page = client.get("/samples").text
    assert "Original (this computer only)" in page and "PERSON_" in page
    r = client.post("/misses", data={"csrf_token": _csrf(client, "/samples"), "reviewer": "Pat",
                                     "term": "Mapleton Clinic", "etype": "ORGANIZATION"}, follow_redirects=False)
    assert r.status_code == 303
    extra = yaml.safe_load((cfg_dir / "review-additions.yaml").read_text())
    assert extra["deny_list"]["ORGANIZATION"] == ["Mapleton Clinic"]
    assert "Mapleton Clinic" in load_config(cfg_dir / "scrubber.yaml")["deny_list"]["ORGANIZATION"]
    assert runner.store.misses()[0]["etype"] == "ORGANIZATION"
    assert "Mapleton Clinic" in client.get("/deny-list").text
    client.post("/deny-list/remove", data={"csrf_token": _csrf(client), "etype": "ORGANIZATION",
                                           "term": "Mapleton Clinic"})
    assert "Mapleton Clinic" not in load_config(cfg_dir / "scrubber.yaml")["deny_list"]["ORGANIZATION"]


def test_rerun_request_handed_to_scrubber(ui, workspace):
    client, *_ = ui
    client.post("/rerun", data={"csrf_token": _csrf(client)})
    assert (workspace.work / "requests" / "rerun.json").exists()


def test_previews(ui):
    client, runner, _, _ = ui
    f = next(x for x in runner.store.files() if x["kind"] == "image")
    for side in ("original", "scrubbed"):
        r = client.get(f"/preview/{file_id(f['rel'])}/{side}.png")
        assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    assert client.get("/preview/0000000000000000/original.png").status_code == 404


def test_reports_served_and_nothing_else(ui):
    client, *_ = ui
    assert client.get("/reports/deidentification-report.pdf").content.startswith(b"%PDF")
    assert client.get("/reports/manifest.csv").status_code == 200
    assert client.get("/reports/..%2Fstate%2Fsecret.key").status_code == 404


def test_review_screen_published_on_loopback_only():
    from pathlib import Path
    compose = yaml.safe_load((Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text())
    assert compose["services"]["review"]["ports"] == ["127.0.0.1:8080:8080"]
    assert compose["services"]["scrubber"]["network_mode"] == "none"
