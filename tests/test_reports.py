import csv
import json
import re

import pymupdf

from conftest import assert_no_secrets
from test_pipeline import _build_input


def _run(workspace, synth, run_pipeline):
    people = _build_input(workspace, synth)
    (workspace.input / "Documents" / "risky.txt").write_text("Seen by our only pediatric surgeon.\n")
    runner, summary = run_pipeline(workspace)
    return people, runner, summary


def test_manifest(workspace, synth, run_pipeline):
    people, runner, summary = _run(workspace, synth, run_pipeline)
    m = json.loads((workspace.output / "manifest.json").read_text())
    assert m["totals"]["files"] == summary["files"]["done"]
    assert m["totals"]["held_for_review"] == 1
    src = m["sources"]
    assert {"Email", "Slack", "Spreadsheets and CRM exports", "Documents", "Images and photos",
            "Medical imaging (DICOM)"} <= set(src)
    assert src["Email"]["messages"] >= 6 and src["Slack"]["messages"] > 0
    assert src["Spreadsheets and CRM exports"]["records"] >= 12
    for b in src.values():
        if b["files"]:
            assert b["output_bytes"] > 0 and re.fullmatch(r"[\d.]+ (B|KB|MB|GB|TB)", b["output_size"])
    lo, hi = m["totals"]["year_range"]
    assert 1900 < lo <= hi <= 2026
    rows = list(csv.reader(open(workspace.output / "manifest.csv")))
    assert rows[0][0] == "source" and rows[-1][0] == "TOTAL"
    assert_no_secrets(json.dumps(m), people, "manifest")


def test_audit_report_contents(workspace, synth, run_pipeline):
    people, runner, summary = _run(workspace, synth, run_pipeline)
    html = (workspace.reports / "deidentification-report.html").read_text()
    for section in ("The 18 Safe Harbor identifiers", "Verification and residual risk", "Human review decisions",
                    "Not exported", "Data manifest", "Configuration", "Software", "Limits and attestation"):
        assert section in html
    assert len(re.findall(r"<tr><td class=\"num\">\d+</td><td>", html)) >= 18
    assert summary["config_hash"] in html and "45 CFR 164.514(b)(2)" in html
    assert "Context risk: unique role or title" in html and "risky.txt" in html  # pending item, scrubbed name
    assert "presidio-analyzer" in html and "Tesseract OCR" in html
    doc = pymupdf.open(workspace.reports / "deidentification-report.pdf")
    import unicodedata
    text = unicodedata.normalize("NFKC", "\n".join(p.get_text() for p in doc))  # expand ligatures (ﬁ)
    assert doc.page_count >= 2 and "18 Safe Harbor identifiers" in text
    for t in (html, text):
        assert_no_secrets(t, people, "audit report")
        assert "pediatric surgeon" not in t  # snippets never reach the report


def test_review_decision_recorded_in_report(workspace, synth, run_pipeline):
    people, runner, summary = _run(workspace, synth, run_pipeline)
    (rv,) = runner.store.reviews("pending")
    runner.approve(rv["rel"], "Pat Compliance", "Title is generic in context")
    runner.write_summary()
    html = (workspace.reports / "deidentification-report.html").read_text()
    assert "Pat Compliance" in html and "approved" in html and "Title is generic in context" in html
    assert "No files are waiting for review" in html
