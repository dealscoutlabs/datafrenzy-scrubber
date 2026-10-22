"""Shared context passed to every format handler."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..state import Store
from ..transform.generalize import Generalizer
from ..transform.pseudonym import Pseudonymizer
from ..transform.scrub import Tally, TextScrubber


class Quarantined(Exception):
    """Raised by a handler when a whole file must not be exported."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Context:
    cfg: dict
    scrubber: TextScrubber
    store: Store
    rel: str                 # input-relative path of the file being processed
    out_rel: str             # scrubbed output-relative path (safe to show in reports)
    tally: Tally = field(default_factory=Tally)

    @property
    def pseudo(self) -> Pseudonymizer:
        return self.scrubber.pseudo

    @property
    def gen(self) -> Generalizer:
        return self.scrubber.gen

    def quarantine(self, item: str, reason: str, mime: str | None = None, size: int | None = None) -> None:
        """Record something inside this file that was removed rather than exported."""
        self.store.quarantine(item, self.rel, reason, mime, size)
        self.tally.counts["QUARANTINED_PARTS"] += 1

    def issue(self, message: str) -> None:
        """A verification or residual-risk problem: the file goes to human review."""
        self.tally.issues.append(message)

    def residual(self, text: str, where: str) -> None:
        """Re-run detection on scrubbed text; anything left is an issue."""
        if not text or not text.strip():
            return
        threshold = float(self.cfg["verification"]["residual_threshold"])
        for f in self.scrubber.detector.detect(text):
            if f.score >= threshold:
                self.issue(f"{where}: residual {f.etype} (score {f.score:.2f}) after scrubbing")
