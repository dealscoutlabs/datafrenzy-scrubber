"""Detection: Presidio's AnalyzerEngine (spaCy en_core_web_lg + built-in and
custom recognizers), followed by Safe Harbor-specific post-processing."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

import spacy
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NerModelConfiguration, SpacyNlpEngine

from ..config import load_name_list
from ..netguard import configure_offline_libraries
from ..transform.generalize import Generalizer
from ..transform.pseudonym import TOKEN_RE, normalize_person
from . import patterns as P
from .recognizers import KnownEntityRecognizer, build_custom_recognizers

log = logging.getLogger(__name__)

SPACY_MODEL = "en_core_web_lg"
MAX_CHUNK = 100_000

# Presidio entity names -> our Safe Harbor entity types. None = ignore.
ENTITY_MAP = {
    "PERSON": "PERSON", "ORGANIZATION": "ORGANIZATION", "LOCATION": "LOCATION", "DATE_TIME": "DATE",
    "PHONE_NUMBER": "PHONE_NUMBER", "EMAIL_ADDRESS": "EMAIL_ADDRESS", "URL": "URL", "IP_ADDRESS": "IP_ADDRESS",
    "CREDIT_CARD": "ACCOUNT_NUMBER", "IBAN_CODE": "ACCOUNT_NUMBER", "CRYPTO": "ACCOUNT_NUMBER",
    "MEDICAL_LICENSE": "LICENSE_NUMBER", "MAC_ADDRESS": "DEVICE_ID", "US_ITIN": "OTHER_ID", "NRP": None,
}
KEEP_BUILTINS = {"CreditCardRecognizer", "UsItinRecognizer", "CryptoRecognizer", "EmailRecognizer",
                 "IbanRecognizer", "IpRecognizer", "MedicalLicenseRecognizer", "MacAddressRecognizer",
                 "PhoneRecognizer", "UrlRecognizer", "SpacyRecognizer"}

# Our own output: never re-detected (keeps re-runs and verification clean).
ARTIFACT_RE = re.compile(
    r"[a-z]+_\d{4,}@[a-z0-9.-]*invalid\b|<?[0-9a-f]{20}@[a-z0-9.-]*invalid>?|" + TOKEN_RE.pattern
    + r"|\[(?:DATE|ZIP)\]|(?<!\d)\d{3}XX\b|(?<!\d)90\+",
    re.IGNORECASE)

# Priority when two findings cover exactly the same span with the same score.
PRIORITY = ["EMAIL_ADDRESS", "URL", "US_SSN", "HEALTH_PLAN_ID", "MEDICAL_RECORD_NUMBER", "LICENSE_NUMBER",
            "VEHICLE_ID", "DEVICE_ID", "BIOMETRIC_ID", "ACCOUNT_NUMBER", "FAX_NUMBER", "PHONE_NUMBER",
            "IP_ADDRESS", "DATE_BIRTH", "DATE", "AGE_OVER_89", "STREET_ADDRESS", "ZIP_CODE", "PERSON",
            "ORGANIZATION", "LOCATION", "OTHER_ID"]
_PRIO = {e: i for i, e in enumerate(PRIORITY)}
NAMEISH = {"PERSON", "ORGANIZATION", "LOCATION", "STREET_ADDRESS"}


@dataclass
class Finding:
    start: int
    end: int
    etype: str
    score: float
    text: str
    source: str


class _SpacyEngine(SpacyNlpEngine):
    """Loads the bundled model without the dependency parser (not needed by
    Presidio, ~30% faster) and never attempts a download."""

    def load(self) -> None:
        self.nlp = {"en": spacy.load(SPACY_MODEL, exclude=["parser"])}


def _ner_config() -> NerModelConfiguration:
    return NerModelConfiguration(
        model_to_presidio_entity_mapping={"PERSON": "PERSON", "PER": "PERSON", "ORG": "ORGANIZATION",
                                          "GPE": "LOCATION", "LOC": "LOCATION", "FAC": "LOCATION",
                                          "DATE": "DATE_TIME", "NORP": "NRP"},
        labels_to_ignore=["CARDINAL", "ORDINAL", "QUANTITY", "MONEY", "PERCENT", "TIME", "LANGUAGE",
                          "PRODUCT", "EVENT", "WORK_OF_ART", "LAW", "NORP"],
        low_score_entity_names=[],
    )


class Detector:
    def __init__(self, cfg: dict, known: Iterable[tuple[str, str, bool]] = ()):
        configure_offline_libraries()
        self.cfg = cfg
        self.thresholds = cfg["thresholds"]
        self.gen = Generalizer(cfg["reference_year"], set())
        allow = cfg["allowlist"]
        self.allow_orgs = {o.lower() for o in allow["organizations"]}
        self.allow_terms = {t.lower() for t in allow["terms"]}
        self.allow_url_domains = [d.lower().lstrip(".") for d in allow["url_domains"]]

        self.nlp_engine = _SpacyEngine(models=[{"lang_code": "en", "model_name": SPACY_MODEL}],
                                       ner_model_configuration=_ner_config())
        self.nlp_engine.load()
        nlp = self.nlp_engine.nlp["en"]

        registry = RecognizerRegistry(supported_languages=["en"])
        registry.load_predefined_recognizers(languages=["en"], nlp_engine=self.nlp_engine)
        registry.recognizers = [r for r in registry.recognizers if type(r).__name__ in KEEP_BUILTINS]

        first = load_name_list("census_first_names.txt")
        last = load_name_list("census_surnames.txt")
        for rec in build_custom_recognizers(cfg, first, last):
            registry.add_recognizer(rec)

        entries = list(known)
        for etype, terms in (cfg.get("deny_list") or {}).items():
            entries += [(t, etype, False) for t in terms or [] if str(t).strip()]
        self.known_count = len(entries)
        if entries:
            registry.add_recognizer(KnownEntityRecognizer(nlp, entries))

        self.analyzer = AnalyzerEngine(registry=registry, nlp_engine=self.nlp_engine,
                                       supported_languages=["en"], default_score_threshold=0)

    # ---- public API
    def detect(self, text: str, threshold_floor: float | None = None) -> list[Finding]:
        if not text or not text.strip():
            return []
        out: list[Finding] = []
        for offset, chunk in _chunks(text):
            results = self.analyzer.analyze(chunk, language="en")
            out += self._post(chunk, results, offset, threshold_floor)
        return out

    def detect_many(self, texts: list[str], threshold_floor: float | None = None) -> list[list[Finding]]:
        out: list[list[Finding] | None] = [None] * len(texts)
        batch_idx = [i for i, t in enumerate(texts) if t and t.strip() and len(t) <= MAX_CHUNK]
        if batch_idx:
            arts = self.nlp_engine.process_batch([texts[i] for i in batch_idx], language="en", batch_size=64)
            for i, (_, artifacts) in zip(batch_idx, arts):
                results = self.analyzer.analyze(texts[i], language="en", nlp_artifacts=artifacts)
                out[i] = self._post(texts[i], results, 0, threshold_floor)
        for i, t in enumerate(texts):
            if out[i] is None:
                out[i] = self.detect(t, threshold_floor) if t and t.strip() else []
        return out  # type: ignore[return-value]

    # ---- post-processing
    def _threshold(self, etype: str) -> float:
        return float(self.thresholds.get(etype, self.thresholds["default"]))

    def _post(self, text: str, results, offset: int, floor: float | None) -> list[Finding]:
        found: list[Finding] = []
        for r in results:
            etype = ENTITY_MAP.get(r.entity_type, r.entity_type)
            if etype is None:
                continue
            source = (r.recognition_metadata or {}).get("recognizer_name", "")
            start, end = r.start, r.end
            span = text[start:end]
            # trim whitespace/punctuation the NER model sometimes includes
            while span and span[-1] in " \t\r\n.,;:)\"'":
                if etype in ("DATE", "LOCATION", "ORGANIZATION", "PERSON") or span[-1] in " \t\r\n,;:\"":
                    end -= 1
                    span = text[start:end]
                else:
                    break
            if span.endswith("'s") and etype in ("PERSON", "ORGANIZATION"):
                end -= 2
                span = text[start:end]
            while span and (span[0] in " \t\r\n\"'" or (span[0] == "(" and ")" not in span)):
                start += 1
                span = text[start:end]
            if not span:
                continue
            etype = self._refine(text, start, end, span, etype, source)
            if etype is None:
                continue
            threshold = self._threshold(etype) if floor is None else min(floor, self._threshold(etype))
            if r.score < threshold:
                continue
            found.append(Finding(start, end, etype, r.score, span, source))
        found = [f for f in found if not _overlaps_artifact(text, f)
                 and not (f.source == "SpacyRecognizer" and f.etype in NAMEISH and not _meaningful(f.text))]
        found = _resolve(found)
        for f in found:
            f.start += offset
            f.end += offset
        return found

    def _refine(self, text: str, start: int, end: int, span: str, etype: str, source: str) -> str | None:
        low = span.lower().strip()
        if low in self.allow_terms:
            return None
        if etype == "DATE":
            if not self.gen.has_date_element(span):
                return None
            if source == "SpacyRecognizer" and not _ner_date_ok(span):
                return None  # "1/2 tablet": numeric month/day without a year needs date context (regex recognizer)
            if re.search(P.BIRTH_CONTEXT, text[max(0, start - 40):start]):
                return "DATE_BIRTH"
            return "DATE"
        if etype == "PHONE_NUMBER":
            if len(re.sub(r"\D", "", span)) < 7:
                return None
            if re.search(P.FAX_CONTEXT, text[max(0, start - 30):start]):
                return "FAX_NUMBER"
            return etype
        if etype == "LOCATION":
            if low.removeprefix("the ") in P.LARGE_PLACES or span.strip().upper() in P.US_STATES:
                return None
            return etype
        if etype == "ORGANIZATION":
            if low in self.allow_orgs or low.removeprefix("the ") in self.allow_orgs:
                return None
            if source == "SpacyRecognizer" and (re.fullmatch(r"[A-Z0-9&/-]{1,4}", span.strip())
                                                or low in P.LARGE_PLACES):
                return None  # clinical abbreviations (ICU, MRI, ER ...) read as orgs
            return etype
        if etype == "PERSON":
            if not normalize_person(span):
                return None
            if source == "SpacyRecognizer" and not re.search(r"[A-Za-z]{2}", span):
                return None
            return etype
        if etype == "URL":
            host = re.sub(r"^[a-z]+://", "", low).split("/")[0]
            if any(host == d or host.endswith("." + d) for d in self.allow_url_domains):
                return None
            if re.fullmatch(r"cid:.*", low):
                return None
            return etype
        if etype == "ZIP_CODE":
            return etype
        return etype


_NUMERIC_MD = re.compile(r"(?<![\d/])\d{1,2}/\d{1,2}(?![\d/])")


def _ner_date_ok(span: str) -> bool:
    from ..transform.generalize import COMPACT_DATE_RE, HOLIDAY_RE, MONTH_RE, NUMERIC_DATE_RE
    if MONTH_RE.search(span) or HOLIDAY_RE.search(span) or COMPACT_DATE_RE.search(span):
        return True
    m = NUMERIC_DATE_RE.search(span)
    return bool(m and (m.group(3) or len(m.group(1)) == 4))


def _chunks(text: str):
    if len(text) <= MAX_CHUNK:
        yield 0, text
        return
    pos = 0
    while pos < len(text):
        end = min(len(text), pos + MAX_CHUNK)
        if end < len(text):
            nl = text.rfind("\n", pos + MAX_CHUNK // 2, end)
            if nl > 0:
                end = nl + 1
        yield pos, text[pos:end]
        pos = end


def _overlaps_artifact(text: str, f: Finding) -> bool:
    lo, hi = max(0, f.start - 40), min(len(text), f.end + 40)
    for m in ARTIFACT_RE.finditer(text, lo, hi):
        if m.start() < f.end and f.start < m.end():
            # an artifact inside a longer real finding (e.g. "PERSON_0001 Smith") is fine to keep scrubbing
            residual = (text[f.start:m.start()] + text[m.end():f.end]).strip(" .,_-")
            if not re.search(r"[A-Za-z0-9]{2}", residual):
                return True
            if f.etype in ("OTHER_ID", "DATE", "ORGANIZATION", "LOCATION"):
                return True
    return False


def _meaningful(span: str) -> bool:
    """Does an NER span contain a word that could be a name (not a label, token or number)?"""
    rest = ARTIFACT_RE.sub(" ", span)
    words = re.findall(r"[A-Za-z][A-Za-z'.-]*", rest)
    return any(w.lower().strip(".'") not in P.LABEL_WORDS and not (w.isupper() and len(w) <= 3) for w in words)


def _resolve(found: list[Finding]) -> list[Finding]:
    """Choose non-overlapping findings: longest span first, then score, then type priority.
    A spaCy span that contains a more precise regex finding of the same kind loses to it."""
    def kind(e):
        return "DATE" if e in ("DATE", "DATE_BIRTH") else e
    precise = [f for f in found if f.source != "SpacyRecognizer"]
    keep = []
    for f in found:
        if f.source == "SpacyRecognizer" and any(
                # a date/place regex inside a looser NER date/place span is more precise
                # (names are not: "Angela Smith" must not shrink to a known "Smith")
                (kind(f.etype) in ("DATE", "LOCATION") and p.start >= f.start and p.end <= f.end
                 and kind(p.etype) == kind(f.etype) and (p.end - p.start) < (f.end - f.start))
                # an NER name span swallowing a number/date/contact finding: the precise one wins
                or (p.etype not in NAMEISH and p.start < f.end and f.start < p.end)
                for p in precise):
            continue
        keep.append(f)
    keep.sort(key=lambda f: (-(f.end - f.start), -f.score, _PRIO.get(f.etype, 99), f.start))
    chosen: list[Finding] = []
    for f in keep:
        if all(f.end <= c.start or f.start >= c.end for c in chosen):
            chosen.append(f)
    chosen.sort(key=lambda f: f.start)
    return chosen
