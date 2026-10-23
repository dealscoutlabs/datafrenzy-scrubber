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
    # labels kept as they are from category columns ("Successful Completion"): not re-flagged
    kept: set = field(default_factory=set)

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

    def issue(self, message: str, kind: str = "verification", snippet: str | None = None) -> None:
        """A verification or residual-risk problem: the file goes to human review.
        `message` must not contain identifiers (it reaches the audit report);
        `snippet` is scrubbed output context shown only on the local review screen."""
        self.tally.issues.append(message)
        self.tally.flags.append({"kind": kind, "message": message, "snippet": snippet})

    def residual(self, text: str, where: str) -> None:
        """Re-run detection on scrubbed text; anything left is an issue. Then the
        context-risk checks: text that could still point to one person."""
        if not text or not text.strip():
            return
        threshold = float(self.cfg["verification"]["residual_threshold"])
        for f in self.scrubber.detector.detect(text):
            if f.score >= threshold:
                self.issue(f"{where}: residual {f.etype} (score {f.score:.2f}) after scrubbing", "residual",
                           _snippet(text, f.start, f.end))
        for r in _risk(self.cfg).check(text):
            self.issue(f"{where}: context risk - {r.rule}", "context-risk", _snippet(text, r.start, r.end))

    def residual_many(self, texts: list[str], where: str) -> None:
        """Like residual(), but each text (a spreadsheet cell) is checked on its own, the
        way it was scrubbed - reading unrelated cells together invents context."""
        texts = [t for t in texts if t and t.strip()]
        if not texts:
            return
        threshold = float(self.cfg["verification"]["residual_threshold"])
        for text, findings in zip(texts, self.scrubber.detector.detect_many(texts)):
            for f in findings:
                if f.score >= threshold:
                    self.issue(f"{where}: residual {f.etype} (score {f.score:.2f}) after scrubbing", "residual",
                               _snippet(text, f.start, f.end))
        risk = _risk(self.cfg)
        for text in texts:
            for r in risk.check(text):
                self.issue(f"{where}: context risk - {r.rule}", "context-risk", _snippet(text, r.start, r.end))


_RISK: dict = {}


def _risk(cfg: dict):
    from ..detect.context_risk import ContextRisk
    key = id(cfg)
    if key not in _RISK:
        _RISK.clear()
        _RISK[key] = ContextRisk(cfg)
    return _RISK[key]


def _snippet(text: str, start: int, end: int, pad: int = 80) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    return ("…" if a else "") + text[a:start] + "⟦" + text[start:end] + "⟧" + text[end:b] + ("…" if b < len(text) else "")
