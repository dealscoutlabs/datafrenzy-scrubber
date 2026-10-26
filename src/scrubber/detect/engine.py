"""Detection: Presidio's AnalyzerEngine (spaCy en_core_web_lg + built-in and
custom recognizers), followed by Safe Harbor-specific post-processing."""

from __future__ import annotations

import bisect
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
MAX_CHUNK = 20_000  # smaller pieces keep per-piece work small; split at line breaks

# Presidio entity names -> our Safe Harbor entity types. None = ignore.
ENTITY_MAP = {
    "PERSON": "PERSON", "ORGANIZATION": "ORGANIZATION", "LOCATION": "LOCATION", "DATE_TIME": "DATE",
    "PHONE_NUMBER": "PHONE_NUMBER", "EMAIL_ADDRESS": "EMAIL_ADDRESS", "URL": "URL", "IP_ADDRESS": "IP_ADDRESS",
    "CREDIT_CARD": "ACCOUNT_NUMBER", "IBAN_CODE": "ACCOUNT_NUMBER", "CRYPTO": "ACCOUNT_NUMBER",
    "MEDICAL_LICENSE": "LICENSE_NUMBER", "MAC_ADDRESS": "DEVICE_ID", "US_ITIN": "OTHER_ID", "NRP": None,
}
KEEP_BUILTINS = {"CreditCardRecognizer", "UsItinRecognizer", "CryptoRecognizer", "EmailRecognizer",
                 "IbanRecognizer", "IpRecognizer", "MedicalLicenseRecognizer", "MacAddressRecognizer",
                 "PhoneRecognizer", "SpacyRecognizer"}  # URLs: our own recognizers (Presidio's is slow on long text)

# Our own output: never re-detected (keeps re-runs and verification clean).
ARTIFACT_RE = re.compile(
    r"[a-z]+_\d{4,}@[a-z0-9.-]*invalid\b|<?[0-9a-f]{20}@[a-z0-9.-]*invalid>?|https?://scrubbed\.invalid/\w*|"
    r"(?-i:<(?:[@#]|!subteam\^)[UWBCGDTEFSARLKQ]Z[0-9A-F]{10}(?=[>|]))|" + TOKEN_RE.pattern
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
    """Loads the bundled model (entity recognizer only) and never attempts a download."""

    def load(self) -> None:
        # Only the entity recognizer is needed: in en_core_web_lg it has its own
        # embedding layer, so dropping the tagger/parser/lemmatizer gives identical
        # entities about a third faster.
        self.nlp = {"en": spacy.load(SPACY_MODEL, exclude=["tok2vec", "tagger", "parser", "attribute_ruler",
                                                          "lemmatizer", "senter"])}


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
        # phone numbers: North American formats only (the international matcher is slow
        # and our own US regex runs as well)
        from presidio_analyzer.predefined_recognizers import PhoneRecognizer
        registry.recognizers = [r for r in registry.recognizers if not isinstance(r, PhoneRecognizer)]
        registry.add_recognizer(PhoneRecognizer(supported_regions=("US", "CA")))

        first = load_name_list("census_first_names.txt")
        last = load_name_list("census_surnames.txt")
        self.first_names, self.last_names = first, last
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
            artifacts = self.nlp_engine.process_text(chunk, "en")
            out += self._post(chunk, self._run_recognizers(chunk, artifacts), offset, threshold_floor)
        return out

    def _run_recognizers(self, text: str, artifacts) -> list:
        """Run every Presidio recognizer in the registry. Presidio's context booster and
        duplicate removal are skipped: both grow quadratically with the number of findings,
        and the duplicate removal discards a precise match inside a longer model span
        (a city inside "Greenfort, MA 18034"). Overlaps are resolved in _resolve()."""
        results = []
        for rec in self.analyzer.registry.recognizers:
            found = rec.analyze(text=text, entities=rec.supported_entities, nlp_artifacts=artifacts)
            results += found or []
        return results

    def detect_many(self, texts: list[str], threshold_floor: float | None = None) -> list[list[Finding]]:
        out: list[list[Finding] | None] = [None] * len(texts)
        batch_idx = [i for i, t in enumerate(texts) if t and t.strip() and len(t) <= MAX_CHUNK]
        if batch_idx:
            arts = self.nlp_engine.process_batch([texts[i] for i in batch_idx], language="en", batch_size=64)
            for i, (_, artifacts) in zip(batch_idx, arts):
                out[i] = self._post(texts[i], self._run_recognizers(texts[i], artifacts), 0, threshold_floor)
        for i, t in enumerate(texts):
            if out[i] is None:
                out[i] = self.detect(t, threshold_floor) if t and t.strip() else []
        return out  # type: ignore[return-value]

    # ---- post-processing
    def _meaningful(self, span: str) -> bool:
        """Does an NER span contain a word that could be a name (not a label, token or number)?
        Next to one of our tokens, the leftover word must be a known first/last name: the model
        often stretches a span from a token into the following word ("PERSON_0008 Footnote")."""
        rest = ARTIFACT_RE.sub(" ", span)
        words = [w.strip(".'-") for w in re.findall(r"[A-Za-z][A-Za-z'.-]*", rest)]
        words = [w for w in words if w.lower() not in P.LABEL_WORDS and not (w.isupper() and len(w) <= 3)]
        if rest == span:
            return bool(words)
        return any(w.upper() in self.first_names or w.upper() in self.last_names for w in words)

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
            if source == "SpacyRecognizer" and "\n" in span.strip():
                # NER spans run across line breaks into the next line's label ("Jeffery Hernandez\nMRN")
                head = span.split("\n", 1)[0]
                if head.strip() and self._meaningful(head):
                    end = start + len(head.rstrip())
                else:
                    tail = span.rsplit("\n", 1)[1]
                    start = end - len(tail)
                span = text[start:end]
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
        artifacts = [(m.start(), m.end()) for m in ARTIFACT_RE.finditer(text)]
        found = [f for f in found if not _overlaps_artifact(text, f, artifacts)
                 and not (f.source == "SpacyRecognizer" and f.etype in NAMEISH and not self._meaningful(f.text))
                 and not (f.source == "SpacyRecognizer" and f.etype in NAMEISH and _beside_dose(text, f))]
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


def _overlaps_artifact(text: str, f: Finding, artifacts: list[tuple[int, int]]) -> bool:
    """artifacts: sorted (start, end) spans of our own tokens in text."""
    k = bisect.bisect_left(artifacts, (f.start, -1))
    for a, b in artifacts[max(0, k - 1):]:
        if a >= f.end:
            break
        if b <= f.start:
            continue
        # an artifact inside a longer real finding (e.g. "PERSON_0001 Smith") is fine to keep scrubbing
        residual = (text[f.start:a] + text[b:f.end]).strip(" .,_-")
        if not re.search(r"[A-Za-z0-9]{2}", residual):
            return True
        if f.etype in ("OTHER_ID", "DATE", "ORGANIZATION", "LOCATION"):
            return True
    return False


def _resolve(found: list[Finding]) -> list[Finding]:
    """Choose non-overlapping findings: longest span first, then score, then type priority.
    A model (spaCy) span loses to a more precise regex finding of the same date/place kind
    inside it, and to any number/contact finding it overlaps. Sorted lookups keep this
    near-linear on long texts with thousands of findings."""
    def kind(e):
        return "DATE" if e in ("DATE", "DATE_BIRTH") else e
    precise = sorted((f for f in found if f.source != "SpacyRecognizer"), key=lambda f: f.start)
    starts = [p.start for p in precise]
    longest = max((p.end - p.start for p in precise), default=0)
    keep = []
    for f in found:
        if f.source == "SpacyRecognizer":
            beaten = False
            for p in precise[bisect.bisect_left(starts, f.start - longest):]:
                if p.start >= f.end:
                    break
                if p.end <= f.start:
                    continue
                inside = p.start >= f.start and p.end <= f.end and (p.end - p.start) < (f.end - f.start)
                if (kind(f.etype) in ("DATE", "LOCATION") and inside and kind(p.etype) == kind(f.etype)) \
                        or p.etype not in NAMEISH:
                    beaten = True
                    break
            if beaten:
                continue
        keep.append(f)
    # a firm-name match that cuts through a person's name ("Jennifer [Duarte and Jordan]") is wrong
    people = [f for f in keep if f.etype == "PERSON"]
    keep = [f for f in keep if f.source != "firm_names" or not any(
        p.start < f.end and f.start < p.end and not (p.start >= f.start and p.end <= f.end) for p in people)]
    keep.sort(key=lambda f: (-(f.end - f.start), -f.score, _PRIO.get(f.etype, 99), f.start))
    chosen_starts: list[int] = []
    chosen: list[Finding] = []
    for f in keep:
        k = bisect.bisect_left(chosen_starts, f.start)
        if k > 0 and chosen[k - 1].end > f.start:
            continue
        if k < len(chosen) and chosen[k].start < f.end:
            continue
        chosen_starts.insert(k, f.start)
        chosen.insert(k, f)
    return chosen


# a drug strength right next to a word: "Afluria PF 30 mcg", "100 MG Extended Release", "Albuterol 0.09 MG"
DOSE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug|g|ml|l|meq|mmol|iu|units?|%|hr|actuat)\b(?:/\S+)?", re.I)


def _beside_dose(text: str, f) -> bool:
    """The language model reads drug brand names as people ("Afluria"). A word with a dose
    within a few characters on the same line is a drug, not a person."""
    left = text[max(0, f.start - 16):f.start].split("\n")[-1]
    right = text[f.end:f.end + 20].split("\n")[0]
    return bool(DOSE.search(left) or DOSE.search(right) or DOSE.search(text[f.start:f.end]))
