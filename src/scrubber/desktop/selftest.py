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


def run(out_path: str | None = None) -> int:
    from ..config import load_config
    from ..pipeline import Paths, Runner
    from .app import network_lock

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        paths = Paths(t / "input", t / "output", t / "work", t / "state")
        paths.input.mkdir()
        (paths.input / "note.txt").write_text(NOTE)
        (paths.input / "patients.csv").write_text(CSV, newline="")
        (paths.input / "visit.eml").write_text(EML, newline="")
        summary = Runner(paths, load_config(), workers=2, echo=lambda *_: None).run()
        leaks = []
        for f in paths.output.rglob("*"):
            if f.is_file():
                text = f.read_bytes().decode("utf-8", "replace")
                leaks += [f"{s} in {f.name}" for s in SECRETS if re.search(re.escape(s), text)]
        lock_ok, lock = network_lock()
        result = {"exported": summary["files"].get("done", 0), "expected": 3, "leaks": leaks,
                  "network_lock": lock, "ok": summary["files"].get("done", 0) == 3 and not leaks}
    text = json.dumps(result, indent=2)
    if out_path:
        Path(out_path).write_text(text)
    if sys.stdout is not None:  # the packaged app has no console
        print(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(run())
