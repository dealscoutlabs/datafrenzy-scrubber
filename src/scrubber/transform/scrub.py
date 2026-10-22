"""Text scrubbing: detect, then replace each finding in place with a consistent
pseudonym token or a Safe Harbor generalization (presidio-anonymizer)."""

from __future__ import annotations

import random
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

    SAMPLE_CAP = 5

    def __init__(self):
        self.counts: Counter = Counter()
        self.years: set[int] = set()
        self.records = 0          # rows / messages / lines exported
        self.messages = 0         # email messages (for mbox verification)
        self.issues: list[str] = []
        self.flags: list[dict] = []    # structured issues for the review screen (local only)
        self.samples: list[dict] = []  # before/after text pairs for the review screen (local only)
        self._seen = 0
        self._rng = random.Random(0)

    def sample(self, original: str, scrubbed: str) -> None:
        """Reservoir sample of changed text, deterministic per run."""
        self._seen += 1
        pair = {"original": original[:1500], "scrubbed": scrubbed[:1500]}
        if len(self.samples) < self.SAMPLE_CAP:
            self.samples.append(pair)
        else:
            j = self._rng.randrange(self._seen)
            if j < self.SAMPLE_CAP:
                self.samples[j] = pair

    def add(self, other: "Tally") -> None:
        self.counts.update(other.counts)
        self.years |= other.years
        self.records += other.records
        self.messages += other.messages
        self.issues += other.issues
        self.flags += other.flags
        self.samples = (self.samples + other.samples)[: self.SAMPLE_CAP * 2]

    def to_dict(self) -> dict:
        return {"counts": dict(self.counts), "years": sorted(self.years), "records": self.records,
                "messages": self.messages, "issues": self.issues, "flags": self.flags, "samples": self.samples}


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
            self._count(findings, tally)
            if len(text.strip()) >= 30:
                tally.sample(text, new)
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

    def scrub_joined(self, texts: list[str], tally: Tally | None = None, max_chars: int = 20000) -> list[Scrubbed]:
        """Scrub short strings read together (joined by blank lines), so the language model
        sees the same context the verification pass sees. Each result belongs to its own string."""
        from dataclasses import replace as _replace

        results: list[Scrubbed | None] = [None] * len(texts)
        group: list[int] = []
        size = 0

        def run(g: list[int]) -> None:
            bounds, pos = [], 0
            for i in g:
                bounds.append((pos, pos + len(texts[i])))
                pos += len(texts[i]) + 2
            joined = "\n\n".join(texts[i] for i in g)
            per: dict[int, list[Finding]] = {i: [] for i in g}
            for f in self.detector.detect(joined):
                for i, (a, b) in zip(g, bounds):
                    if a <= f.start < b:
                        end = min(f.end, b)
                        per[i].append(_replace(f, start=f.start - a, end=end - a, text=joined[f.start:end]))
                        break
            for i in g:
                results[i] = self._apply(texts[i], per[i], tally)

        for i, t in enumerate(texts):
            if not t or not t.strip():
                results[i] = Scrubbed(t, [])
                continue
            if group and size + len(t) > max_chars:
                run(group)
                group, size = [], 0
            group.append(i)
            size += len(t) + 2
        if group:
            run(group)
        return results  # type: ignore[return-value]

    def plan_many(self, texts: list[str], tally: Tally | None = None) -> list[list[tuple[int, int, str]]]:
        """Like scrub_many, but returns (start, end, replacement) spans instead of new text.
        Used where text is split over formatted pieces (Word runs, PDF words)."""
        out = []
        for text, findings in zip(texts, self.detector.detect_many(texts)):
            spans = []
            for f in findings:
                f.text = text[f.start:f.end]
                spans.append((f.start, f.end, self.replacement(f.etype, f.text)))
            if tally is not None:
                self._count(findings, tally)
            out.append(spans)
        return out

    def _count(self, findings: list[Finding], tally: Tally) -> None:
        for f in findings:
            tally.counts[f.etype] += 1
            if f.etype in ("DATE", "DATE_BIRTH"):
                y = self.gen.year_of(f.text)
                if y:
                    tally.years.add(y)

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


def apply_spans_to_segments(segments: list[str], spans: list[tuple[int, int, str]]) -> list[str]:
    """Apply replacements found in "".join(segments) back onto the segments.

    A replacement goes into the segment where the identifier starts (so it takes
    that run's formatting); the rest of the identifier is removed from later
    segments. Unchanged text stays in its own segment.
    """
    if not spans:
        return list(segments)
    owner: list[int] = []
    for i, seg in enumerate(segments):
        owner.extend([i] * len(seg))
    text = "".join(segments)
    out = [""] * len(segments)
    pos = 0
    for start, end, repl in sorted(spans):
        if start < pos:
            continue
        for k in range(pos, start):
            out[owner[k]] += text[k]
        if start < len(owner):
            out[owner[start]] += repl
        elif segments:
            out[-1] += repl
        pos = end
    for k in range(pos, len(text)):
        out[owner[k]] += text[k]
    return out
