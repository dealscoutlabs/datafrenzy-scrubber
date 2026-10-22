import pytest

from scrubber.config import load_config
from scrubber.detect.context_risk import ContextRisk

R = ContextRisk(load_config(overrides={"reference_year": 2026}))

FLAGGED = [
    ("unique role or title", "Referred to our only pediatric surgeon for review."),
    ("unique role or title", "The CEO called about her own claim."),
    ("rare condition with a location", "PERSON_0001 has progeria and lives in LOCATION_0002."),
    ("rare condition with a location", "Diagnosed with Huntington's disease; family moved to Vermont."),
    ("small-town reference", "Everybody knows everybody in a small town like ours."),
    ("small-town reference", "We are the only clinic in the county."),
    ("distinctive event", "He was injured in the plane crash last year."),
    ("distinctive event", "Her story went viral after the surgery."),
    ("relationship reference", "Dr. PERSON_0003's daughter was seen today."),
    ("relationship reference", "The owner's son handles billing."),
]
CLEAN = [
    "Patient reports mild headache, follow up in two weeks.",
    "Diagnosed with progeria.",                      # rare condition but no place
    "PERSON_0001 lives in LOCATION_0002 and has asthma.",  # place but common condition
    "Please send the invoice to the billing office.",
]


@pytest.mark.parametrize("rule,text", FLAGGED)
def test_flags(rule, text):
    assert rule in {r.rule for r in R.check(text)}


@pytest.mark.parametrize("text", CLEAN)
def test_clean(text):
    assert R.check(text) == []


def test_keyword_lists_and_disabled_rules_configurable():
    cfg = load_config(overrides={"reference_year": 2026, "context_risk": {
        "small_towns": ["Bird-in-Hand"], "unique_roles": ["head of radiology"],
        "disabled_rules": ["distinctive event"]}})
    r = ContextRisk(cfg)
    assert {x.rule for x in r.check("Lives near Bird-in-Hand.")} == {"small-town reference"}
    assert {x.rule for x in r.check("Ask the head of radiology.")} == {"unique role or title"}
    assert r.check("Injured in the plane crash.") == []


def test_context_risk_holds_file_for_review(workspace, run_pipeline):
    (workspace.input / "note.txt").write_text("Consult with our only pediatric surgeon about the case.\n")
    runner, summary = run_pipeline(workspace)
    assert summary["files"] == {"review": 1}
    (rv,) = runner.store.reviews("pending")
    assert "context risk - unique role or title" in rv["reasons"]
    import json
    details = json.loads(rv["details"])
    assert details[0]["kind"] == "context-risk" and "⟦our only pediatric surgeon⟧" in details[0]["snippet"]
