"""The five sample notes from John Snow Labs' de-identification demo: everything their tool
found must go, and the clinical content around it (field labels, drugs, abbreviations) must stay."""

import json
import re
from pathlib import Path

import pytest

HERE = Path(__file__).parent / "fixtures" / "jsl"
PHI = json.loads((HERE / "phi.json").read_text())


def note(name):
    return (HERE / name).read_text()


@pytest.mark.parametrize("name", sorted(PHI))
def test_everything_jsl_found_is_removed(scrubber, name):
    out = scrubber.scrub(note(name)).text
    left = [v for v in PHI[name] if v in out]
    assert not left, f"{name}: still in the output: {left}"


@pytest.mark.parametrize("name,patient,label", [
    ("sample2.txt", "Yosef Villegas", ". Age: 46"),
    ("sample3.txt", "Elvis Presley", ". Age: 17"),
    ("sample5.txt", "Ava Davis", ". EMAIL: "),
])
def test_a_labeled_name_stops_at_the_name(scrubber, name, patient, label):
    """"Name: Elvis Presley. Age: 17": the next field's label stays, and the patient
    gets the same token in the header as everywhere else in the note."""
    out = scrubber.scrub(note(name)).text
    m = re.search(r"Name ?: (PERSON_\d+)" + re.escape(label), out)
    assert m, out[:200]
    assert out.count(m.group(1)) == note(name).count(patient)


def test_trailing_hyphen_is_not_part_of_the_name(scrubber):
    out = scrubber.scrub(note("sample1.txt")).text
    m = re.search(r"Name : (PERSON_\d+)- 58-yr-old", out)
    assert m and out.count(m.group(1)) == note("sample1.txt").count("Mark Villegas")


@pytest.mark.parametrize("name,kept", [
    ("sample1.txt", ["Hydroxychloroquine", "Prednisone", "Inflammatory arthritis"]),
    ("sample2.txt", ["NIDDM", "Metformin", "MIBI", "RBBB"]),
])
def test_drugs_and_abbreviations_are_kept(scrubber, name, kept):
    out = scrubber.scrub(note(name)).text
    assert all(k in out for k in kept), [k for k in kept if k not in out]


def test_hospitals_are_organizations_whole(detector):
    found = detector.detect(note("sample4.txt")) + detector.detect(note("sample2.txt"))
    assert not [f.text for f in found if f.etype == "PERSON" and "Hospital" in f.text]
    orgs = [f.text for f in found if f.etype == "ORGANIZATION"]
    assert "Johns Hopkins Hospital" in orgs and "Cedars-Sinai Medical Center" in orgs


def test_drug_endings_do_not_hide_real_names(detector):
    found = detector.detect("Seen today by Dr. Tronolone. Mr. Bartolone declined Hydroxychloroquine.")
    people = {f.text for f in found if f.etype == "PERSON"}
    assert {"Tronolone", "Bartolone"} <= {p.split()[-1] for p in people}
    assert not any("Hydroxychloroquine" in f.text for f in found)
