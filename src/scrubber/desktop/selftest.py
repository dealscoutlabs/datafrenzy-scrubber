"""`DataFrenzyScrubber.exe --self-test`: scrub a few made-up files end to end
without opening a window, and check nothing identifying survived. Used by the
automated Windows build and by support ("run the self-test and send the result")."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

# Invented people only - never real data.
NOTE = ("Patient Marisol Fenwick (MRN 00483921, DOB 07/04/1958) was seen on March 14, 2021 at "
        "4821 Willow Creek Road, Port Kevinberg, OR 97031. Call (415) 555-0172 or email "
        "m.fenwick88@examplemail.com. SSN 523-71-4402. Her son Desmond Okafor drove her home.\n")
CSV = ("Patient Name,DOB,Phone,MRN,Notes\r\n"
       "Marisol Fenwick,1958-07-04,(415) 555-0172,00483921,Spoke with Desmond Okafor on 3/14/2021\r\n")
EML = ("From: Desmond Okafor <d.okafor@examplemail.com>\r\nTo: Clinic <front@exampleclinic.org>\r\n"
       "Subject: Appointment for Marisol Fenwick\r\nDate: Mon, 15 Mar 2021 10:00:00 -0500\r\n"
       "Message-ID: <abc123@examplemail.com>\r\n\r\n" + NOTE)
SECRETS = ["Marisol", "Fenwick", "Desmond", "Okafor", "00483921", "555-0172", "523-71-4402", "examplemail",
           "Willow Creek", "Kevinberg", "97031", "March 14"]


def _scan(path: Path) -> None:
    """A picture of a typed note, like a scanned page."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1400, 220), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=40)
    except TypeError:
        font = ImageFont.load_default()
    draw.text((40, 40), "Patient: Marisol Fenwick", fill="black", font=font)
    draw.text((40, 120), "Phone (415) 555-0172", fill="black", font=font)
    img.save(path)


def run(out_path: str | None = None) -> int:
    from ..config import load_config
    from ..pipeline import Paths, Runner
    from .app import network_lock

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        t = Path(tmp)
        paths = Paths(t / "input", t / "output", t / "work", t / "state")
        paths.input.mkdir()
        (paths.input / "note.txt").write_text(NOTE)
        (paths.input / "patients.csv").write_text(CSV, newline="")
        (paths.input / "visit.eml").write_text(EML, newline="")
        _scan(paths.input / "scan.png")  # needs the bundled OCR engine
        runner = Runner(paths, load_config(), workers=2, echo=lambda *_: None)
        summary = runner.run()
        runner.store.close()  # Windows won't delete an open database file
        leaks = []
        for f in paths.output.rglob("*"):
            if f.is_file():
                text = f.read_bytes().decode("utf-8", "replace")
                leaks += [f"{s} in {f.name}" for s in SECRETS if re.search(re.escape(s), text)]
        scan = runner.store.file("scan.png")
        ocr = {"status": scan["status"] if scan else "missing", "reason": (scan["reason"] or "") if scan else ""}
        lock_ok, lock = network_lock()
        exported = summary["files"].get("done", 0)
        result = {"exported": exported, "expected": 4, "leaks": leaks, "ocr": ocr, "network_lock": lock,
                  "ok": exported >= 3 and ocr["status"] in ("done", "review") and not leaks}
    text = json.dumps(result, indent=2)
    if out_path:
        Path(out_path).write_text(text)
    if sys.stdout is not None:  # the packaged app has no console
        print(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(run())
