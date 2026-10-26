"""Large plain zips are unpacked into separate work items (parallel, resumable, one row per file)."""

import zipfile

from scrubber.desktop.app import batch_files


def test_large_zip_is_unpacked_into_files(workspace, synth, run_pipeline):
    people = [synth.person() for _ in range(12)]
    with zipfile.ZipFile(workspace.input / "export.zip", "w") as z:
        for i, p in enumerate(people):
            z.writestr(f"notes/note{i}.txt", synth.note(p))
        z.writestr("../escape.txt", "outside")          # must never be written outside the work folder
    runner, summary = run_pipeline(workspace, workers=2)
    rows = runner.store.files()
    parent = next(r for r in rows if r["rel"] == "export.zip")
    assert parent["status"] == "converted"
    kids = [r for r in rows if r["parent"] == "export.zip"]
    assert len(kids) == 13 and all(r["rel"].startswith("export (from zip)/") for r in kids)
    assert {r["status"] for r in kids} <= {"done", "review"}
    out = list(workspace.released.rglob("note*.txt")) + list((workspace.work / "staging").rglob("note*.txt"))
    assert len(out) == 12
    text = "".join(f.read_text() for f in out)
    for p in people:
        assert p.last not in text and p.ssn not in text
    assert not (workspace.work / "escape.txt").exists() and not (workspace.work / "zip" / "escape.txt").exists()
    # the batch view lists the files inside, not the zip
    names = [r["rel"] for r in batch_files(rows)]
    assert "export.zip" not in names and len(names) == 13


def test_small_zip_stays_one_item(workspace, synth, run_pipeline):
    with zipfile.ZipFile(workspace.input / "two.zip", "w") as z:
        for i in range(2):
            z.writestr(f"n{i}.txt", synth.note(synth.person()))
    runner, _ = run_pipeline(workspace)
    assert [r["status"] for r in runner.store.files() if r["rel"] == "two.zip"] in (["done"], ["review"])


def test_gzip_file_is_unpacked_and_scrubbed(workspace, synth, run_pipeline):
    import gzip
    p = synth.person()
    with gzip.open(workspace.input / "patients.csv.gz", "wt") as fh:
        fh.write(f"Patient Name,SSN,drug\n{p.name},{p.ssn},Fentanyl Citrate\n")
    runner, _ = run_pipeline(workspace)
    rows = {r["rel"]: r for r in runner.store.files()}
    assert rows["patients.csv.gz"]["status"] == "converted" and rows["patients.csv"]["status"] in ("done", "review")
    out = (list(workspace.released.rglob("patients.csv")) + list((workspace.work / "staging").rglob("patients.csv")))[0]
    text = out.read_text()
    assert p.last not in text and p.ssn not in text and "Fentanyl Citrate" in text
