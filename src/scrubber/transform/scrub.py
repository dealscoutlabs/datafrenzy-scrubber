"""Text scrubbing: detect, then replace each finding in place with a consistent
pseudonym token or a Safe Harbor generalization (presidio-anonymizer)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig, RecognizerResult

from ..detect.engine import Detector, Finding
from ..identifiers import TEXT_ENTITIES, TOKEN_PREFIX
from .generalize import YEAR4_RE, Generalizer
from .pseudonym import Pseudonymizer


@dataclass
class Scrubbed:
    text: str
    findings: list[Finding]
    replacements: list[str] = field(default_factory=list)


class Tally:
    """Per-job counters that end up in the audit report and manifest."""

    def __init__(self):
        self.counts: Counter = Counter()
        self.years: set[int] = set()
        self.records = 0          # rows / messages / lines exported
        self.messages = 0         # email messages (for mbox verification)
        self.issues: list[str] = []

    def add(self, other: "Tally") -> None:
        self.counts.update(other.counts)
        self.years |= other.years
        self.records += other.records
        self.messages += other.messages
        self.issues += other.issues

    def to_dict(self) -> dict:
        return {"counts": dict(self.counts), "years": sorted(self.years), "records": self.records,
                "messages": self.messages, "issues": self.issues}


class TextScrubber:
    def __init__(self, detector: Detector, pseudo: Pseudonymizer, gen: Generalizer):
        self.detector = detector
        self.pseudo = pseudo
        self.gen = gen
        self.anonymizer = AnonymizerEngine()
        self.operators = {e: OperatorConfig("custom", {"lambda": self._replacer(e)}) for e in TEXT_ENTITIES}

    def _replacer(self, etype: str):
        return lambda value: self.replacement(etype, value)

    def replacement(self, etype: str, value: str) -> str:
        if etype == "ZIP_CODE":
            return self.gen.zip_text(value)
        if etype == "DATE":
            return self.gen.date_text(value)
        if etype == "DATE_BIRTH":
            return self.gen.date_text(value, birth=True)
        if etype == "AGE_OVER_89":
            return "90+"
        if etype in TOKEN_PREFIX:
            return self.pseudo.token(etype, value)
        return "[REDACTED]"

    def _apply(self, text: str, findings: list[Finding], tally: Tally | None) -> Scrubbed:
        if not findings:
            return Scrubbed(text, [])
        results = [RecognizerResult(f.etype, f.start, f.end, f.score) for f in findings]
        new = self.anonymizer.anonymize(text=text, analyzer_results=results, operators=self.operators).text
        if tally is not None:
            for f in findings:
                tally.counts[f.etype] += 1
                if f.etype in ("DATE", "DATE_BIRTH"):
                    y = self.gen.year_of(f.text)
                    if y:
                        tally.years.add(y)
        return Scrubbed(new, findings)

    def scrub(self, text: str, tally: Tally | None = None, detect_text: str | None = None) -> Scrubbed:
        """detect_text: same-length variant of text to run detection on (e.g. file names
        with underscores turned into spaces)."""
        if not text or not text.strip():
            return Scrubbed(text, [])
        findings = self.detector.detect(detect_text if detect_text is not None else text)
        for f in findings:
            f.text = text[f.start:f.end]
        return self._apply(text, findings, tally)

    def scrub_many(self, texts: list[str], tally: Tally | None = None) -> list[Scrubbed]:
        all_findings = self.detector.detect_many(texts)
        return [self._apply(t, fs, tally) for t, fs in zip(texts, all_findings)]

    def scrub_name(self, name: str, tally: Tally | None = None) -> str:
        """Scrub a file or folder name, keeping its extension."""
        stem, dot, ext = name.rpartition(".") if "." in name.strip(".") else (name, "", "")
        if not dot:
            stem, ext = name, ""
        detect = re.sub(r"[_]", " ", stem)
        out = self.scrub(stem, tally, detect_text=detect).text
        return f"{out}.{ext}" if dot else out

    def years_in(self, text: str) -> set[int]:
        return {int(y) for y in YEAR4_RE.findall(text)}
