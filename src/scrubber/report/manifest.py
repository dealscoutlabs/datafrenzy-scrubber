"""Data manifest: what the export contains, per source, for buyer quotes.

Counts and sizes only - no names, no dates finer than a year.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import zipfile
from pathlib import Path

from .. import __version__

SOURCE_OF_KIND = {
    "eml": "Email", "mbox": "Email", "csv": "Spreadsheets and CRM exports", "xlsx": "Spreadsheets and CRM exports",
    "json": "Data exports (JSON)", "jsonl": "Data exports (JSON)", "docx": "Documents", "pdf": "Documents",
    "text": "Documents", "image": "Images and photos", "dicom": "Medical imaging (DICOM)",
    "media": "Call recordings (scrubbed transcripts only)", "zip": "Archives",
}
UNIT_OF_SOURCE = {"Email": "messages", "Slack": "messages", "Spreadsheets and CRM exports": "rows",
                  "Data exports (JSON)": "records", "Documents": "pages/paragraphs", "Images and photos": "images",
                  "Medical imaging (DICOM)": "images", "Call recordings (scrubbed transcripts only)": "transcripts",
                  "Archives": "files"}


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def _is_slack(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.rsplit("/", 1)[-1] in ("users.json", "channels.json") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _source(row, released: Path) -> str:
    kind = row["kind"]
    if kind == "zip" and _is_slack(released / row["out_rel"]):
        return "Slack"
    return SOURCE_OF_KIND.get(kind, "Other")


def _blank() -> dict:
    return {"files": 0, "input_bytes": 0, "output_bytes": 0, "records": 0, "messages": 0, "years": set(),
            "held_for_review": 0, "not_exported": 0}


def build(runner) -> dict:
    released = runner.paths.released
    by_source: dict[str, dict] = {}
    by_folder: dict[str, dict] = {}
    total = _blank()
    for row in runner.store.files():
        if row["status"] in ("converted", "removed") or row["kind"] in ("system",):
            continue
        source = _source(row, released) if row["status"] == "done" else SOURCE_OF_KIND.get(row["kind"], "Other")
        folder = (row["out_rel"] or row["rel"]).split("/")[0] if "/" in (row["out_rel"] or "") else "(top level)"
        buckets = [by_source.setdefault(source, _blank()), by_folder.setdefault(folder, _blank()), total]
        if row["status"] == "done":
            out = released / row["out_rel"]
            size_out = out.stat().st_size if out.exists() else 0
            years = set(json.loads(row["years"] or "[]"))
            for b in buckets:
                b["files"] += 1
                b["input_bytes"] += row["size"] or 0
                b["output_bytes"] += size_out
                b["records"] += row["records"] or 0
                b["messages"] += row["messages"] or 0
                b["years"] |= years
        elif row["status"] == "review":
            for b in buckets:
                b["held_for_review"] += 1
        else:
            for b in buckets:
                b["not_exported"] += 1

    def finish(b: dict, name: str | None = None) -> dict:
        years = sorted(y for y in b.pop("years") if 1900 <= y <= dt.date.today().year + 1)
        b["year_range"] = [years[0], years[-1]] if years else None
        b["output_size"] = human_size(b["output_bytes"])
        b["input_size"] = human_size(b["input_bytes"])
        if name:
            b["record_unit"] = UNIT_OF_SOURCE.get(name, "records")
        return b

    return {
        "tool": "Data Frenzy Local Scrubber", "version": __version__,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "method": "HIPAA Safe Harbor, 45 CFR 164.514(b)(2)", "config_hash": runner.chash,
        "totals": finish(total),
        "sources": {k: finish(v, k) for k, v in sorted(by_source.items())},
        "folders": {k: finish(v) for k, v in sorted(by_folder.items())},
    }


def write(m: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(m, indent=2))
    with open(output / "manifest.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "files exported", "size exported", "bytes exported", "records", "record unit",
                    "messages", "first year", "last year", "held for review", "not exported"])
        for name, b in m["sources"].items():
            yr = b["year_range"] or ["", ""]
            w.writerow([name, b["files"], b["output_size"], b["output_bytes"], b["records"], b["record_unit"],
                        b["messages"], yr[0], yr[1], b["held_for_review"], b["not_exported"]])
        t = m["totals"]
        yr = t["year_range"] or ["", ""]
        w.writerow(["TOTAL", t["files"], t["output_size"], t["output_bytes"], t["records"], "", t["messages"],
                    yr[0], yr[1], t["held_for_review"], t["not_exported"]])
