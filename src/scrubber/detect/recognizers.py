"""Custom Presidio recognizers covering the Safe Harbor identifiers that the
built-in recognizers miss or handle loosely."""

from __future__ import annotations

import re
from typing import Callable, Iterable

from presidio_analyzer import EntityRecognizer, RecognizerResult
from spacy.matcher import PhraseMatcher

from . import patterns as P


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def dea_ok(value: str) -> bool:
    d = [int(c) for c in value[2:]]
    return (d[0] + d[2] + d[4] + 2 * (d[1] + d[3] + d[5])) % 10 == d[6]


_VIN_MAP = {**{str(i): i for i in range(10)}, **dict(zip("ABCDEFGH", range(1, 9))),
            **dict(zip("JKLMN", range(1, 6))), "P": 7, "R": 9, **dict(zip("STUVWXYZ", range(2, 10)))}
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check_digit_ok(vin: str) -> bool:
    total = sum(_VIN_MAP[c] * w for c, w in zip(vin, _VIN_WEIGHTS))
    check = total % 11
    return vin[8] == ("X" if check == 10 else str(check))


def _long_alnum_score(v: str) -> float | None:
    if len(re.sub(r"[^A-Za-z0-9]", "", v)) < 8 or sum(c.isdigit() for c in v) < 2:
        return None
    # "94-year-old", "12-month-old": a number joined to ordinary words is not an ID
    if any(len(part) >= 3 and part.isalpha() and (part.islower() or part.istitle()) for part in v.split("-")):
        return None
    return 0.5


class RegexRecognizer(EntityRecognizer):
    """Regex recognizer that can return a named group's span and score by a callback.

    Unlike Presidio's PatternRecognizer, patterns are compiled with their own
    flags (case-sensitive unless the pattern says (?i)).
    """

    def __init__(self, name: str, entity: str, regexes: Iterable[str], score: float, group: str | None = None,
                 scorer: Callable[[str], float | None] | None = None, flags: int = 0):
        self.regexes = [re.compile(r, flags) for r in regexes]
        self.score = score
        self.group = group
        self.scorer = scorer
        super().__init__(supported_entities=[entity], name=name, supported_language="en")

    def load(self) -> None:
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        out = []
        entity = self.supported_entities[0]
        for rx in self.regexes:
            for m in rx.finditer(text):
                grp = self.group if self.group and self.group in rx.groupindex else 0
                start, end = m.span(grp)
                if start == end:
                    continue
                value = text[start:end]
                score = self.scorer(value) if self.scorer else self.score
                if score is None:
                    continue
                out.append(RecognizerResult(entity, start, end, score,
                                            recognition_metadata={"recognizer_name": self.name}))
        return out


def labeled_id_recognizer(entity: str, labels: list[str], value_kind: str, idx: int) -> RegexRecognizer:
    label = "|".join(f"(?:{lab})" for lab in labels)
    idword = r"(?:[\s.]*(?:(?:id|identifier|no|num|number|nbr|code)\b|#))*\.?"
    if value_kind == "digits":
        value = r"\d{3}[- ]?\d{2}[- ]?\d{4}|(?:x{3}|\*{3})[- ]?(?:x{2}|\*{2})[- ]?\d{4}"
        sep = r"\s*(?:is\s+|of\s+|=\s*|:\s*|#\s*|-\s*)?"
    elif value_kind == "plate":
        value = r"(?=[A-Za-z0-9 -]{0,9}\d)[A-Za-z0-9]{2,8}(?:[ -][A-Za-z0-9]{2,4})?"
        sep = r"\s*(?:is\s+|of\s+|=\s*|:\s*|#\s*|-\s*)?"
    elif value_kind == "handle":
        value = r"@?[A-Za-z0-9][A-Za-z0-9._-]{2,40}(?<![.-])"
        sep = r"\s*(?:(?:is|was)\s+|[:=#]\s*)(?=\S)"
    else:
        value = r"(?=[A-Za-z0-9-]*\d)[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){3,30}"
        sep = r"\s*(?:is\s+|of\s+|=\s*|:\s*|#\s*|-\s*)?"
    # a bare year after a label ("visit 2019") is a generalized date, not an ID
    not_year = r"(?!(?:19|20)\d\d(?![A-Za-z0-9-]))"
    regex = rf"(?<![A-Za-z])(?i:(?:{label}){idword}{sep}){not_year}(?P<value>{value})(?![A-Za-z0-9])"
    return RegexRecognizer(f"labeled_{entity.lower()}_{idx}", entity, [regex], 0.85, group="value")


class NameGazetteerRecognizer(EntityRecognizer):
    """Capitalized first+last name pairs from US Census name lists, honorific + name,
    and first names after greetings/sign-offs."""

    def __init__(self, first_names: frozenset[str], last_names: frozenset[str]):
        self.first = first_names
        self.last = last_names
        self.honorific = re.compile(
            rf"\b{P.HONORIFIC}\.?\s+(?P<value>[A-Z][a-zA-Z'-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-zA-Z'-]+){{0,2}})")
        self.greeting = re.compile(rf"{P.GREETING}(?P<value>[A-Z][a-z'-]+(?:[ \t]+[A-Z][a-z'-]+)?)\b", re.M)
        self.signature = re.compile(r"^[ \t]*[-~]*[ \t]*(?P<value>[A-Z][a-z'-]+(?:[ \t]+[A-Z][a-z'-]+)?)[ \t,.]*$", re.M)
        super().__init__(supported_entities=["PERSON"], name="name_gazetteer", supported_language="en")

    def load(self) -> None:
        pass

    def _is_name_word(self, word: str, names: frozenset[str]) -> bool:
        return (word.istitle() or word.isupper()) and len(word) > 1 and word.upper() in names

    def _lower_pair(self, toks, i) -> bool:
        """'talked to lisa french' - lowercase first + last name, common in chat."""
        a, b = toks[i], toks[i + 1]
        if not (a.text.islower() and b.text.islower() and a.is_alpha and b.is_alpha):
            return False
        if len(a.text) < 3 or len(b.text) < 3 or a.is_stop or b.is_stop:
            return False
        if a.text in P.COMMON_WORD_NAMES or a.text.upper() not in self.first or b.text.upper() not in self.last:
            return False
        # both words must be rare as ordinary words (the vector table knows them as names/rare words)
        return not (a.text in P.LOWER_PAIR_BLOCK or b.text in P.LOWER_PAIR_BLOCK)

    def _result(self, start, end, score):
        return RecognizerResult("PERSON", start, end, score,
                                recognition_metadata={"recognizer_name": self.name})

    def analyze(self, text, entities, nlp_artifacts=None):
        out = []
        doc = getattr(nlp_artifacts, "tokens", None) if nlp_artifacts else None
        if doc is not None:
            toks = list(doc)
            i = 0
            while i < len(toks) - 1:
                t = toks[i]
                if self._lower_pair(toks, i):
                    out.append(self._result(t.idx, toks[i + 1].idx + len(toks[i + 1].text), 0.6))
                    i += 2
                    continue
                if self._is_name_word(t.text, self.first):
                    j = i + 1
                    # optional middle initial or middle name
                    if j < len(toks) - 1 and re.fullmatch(r"[A-Z]\.?", toks[j].text):
                        j += 1
                        if toks[j].text == "." and j < len(toks) - 1:
                            j += 1
                    elif (j < len(toks) - 1 and self._is_name_word(toks[j].text, self.first)
                          and self._is_name_word(toks[j + 1].text, self.last)):
                        j += 1
                    if self._is_name_word(toks[j].text, self.last):
                        end_tok = toks[j]
                        # hyphenated surname
                        if j + 2 < len(toks) and toks[j + 1].text == "-" and toks[j + 2].text.istitle():
                            end_tok = toks[j + 2]
                        out.append(self._result(t.idx, end_tok.idx + len(end_tok.text), 0.75))
                        i = j + 1
                        continue
                i += 1
        for m in self.honorific.finditer(text):
            out.append(self._result(*m.span("value"), 0.85))
        for rx, score in ((self.greeting, 0.7), (self.signature, 0.6)):
            for m in rx.finditer(text):
                words = m.group("value").split()
                first = words[0]
                if first.lower() in P.COMMON_WORD_NAMES or first.upper() not in self.first:
                    continue
                start, end = m.span("value")
                if len(words) == 2 and not (words[1].upper() in self.last):
                    end = start + len(first)
                out.append(self._result(start, end, score))
        return out


class KnownEntityRecognizer(EntityRecognizer):
    """Exact matches for names harvested from email headers / name columns and the
    user's deny list. Uses spaCy's PhraseMatcher on the analyzer's tokens."""

    def __init__(self, nlp, entries: list[tuple[str, str, bool]]):
        """entries: (surface text, entity type, case_sensitive_capitalized_only)"""
        self.matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
        self.meta: dict[int, tuple[str, bool]] = {}
        by_key: dict[tuple[str, bool], list[str]] = {}
        for surface, etype, capitalized_only in entries:
            by_key.setdefault((etype, capitalized_only), []).append(surface)
        for (etype, cap), surfaces in by_key.items():
            key = f"{etype}|{int(cap)}"
            self.matcher.add(key, [nlp.make_doc(s) for s in sorted(set(surfaces))])
            self.meta[nlp.vocab.strings[key]] = (etype, cap)
        etypes = sorted({e for e, _ in self.meta.values()}) or ["PERSON"]
        self.empty = not entries
        super().__init__(supported_entities=etypes, name="known_entities", supported_language="en")

    def load(self) -> None:
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        doc = getattr(nlp_artifacts, "tokens", None) if nlp_artifacts else None
        if self.empty or doc is None:
            return []
        out = []
        for match_id, start, end in self.matcher(doc):
            etype, cap = self.meta[match_id]
            span = doc[start:end]
            if cap and not all(t.text[:1].isupper() for t in span if t.is_alpha):
                continue
            out.append(RecognizerResult(etype, span.start_char, span.end_char, 0.9 if cap else 1.0,
                                        recognition_metadata={"recognizer_name": self.name}))
        return out


def build_custom_recognizers(cfg: dict, first_names, last_names) -> list[EntityRecognizer]:
    recs: list[EntityRecognizer] = []
    for i, entry in enumerate(cfg["labeled_ids"]):
        recs.append(labeled_id_recognizer(entry["entity"], entry["labels"], entry.get("value", "id"), i))

    recs += [
        RegexRecognizer("ssn", "US_SSN", [P.SSN], 0.85),
        RegexRecognizer("ssn_spaced", "US_SSN", [P.SSN_SPACED], 0.6),
        RegexRecognizer("medicare_mbi", "HEALTH_PLAN_ID", [P.MBI], 0.85, flags=re.I),
        RegexRecognizer("dea", "LICENSE_NUMBER", [P.DEA], 0.0,
                        scorer=lambda v: 0.9 if dea_ok(v.upper()) else None, flags=re.I),
        RegexRecognizer("vin", "VEHICLE_ID", [P.VIN], 0.0, flags=re.I, scorer=lambda v: (
            0.95 if vin_check_digit_ok(v.upper()) else 0.65
        ) if re.search(r"\d", v) and re.search(r"[A-Za-z]", v) else None),
        RegexRecognizer("social_handle", "OTHER_ID", [P.SOCIAL_HANDLE], 0.6),
        RegexRecognizer("long_alnum_id", "OTHER_ID", [P.LONG_ALNUM], 0.0, scorer=_long_alnum_score),
        RegexRecognizer("long_number_id", "OTHER_ID", [P.LONG_DIGITS], 0.45),
        RegexRecognizer("street_address", "STREET_ADDRESS", [P.STREET_ADDRESS, P.PO_BOX, P.MILITARY_ADDRESS], 0.8),
        RegexRecognizer("city_state", "LOCATION", [P.CITY_STATE_ZIP], 0.75, group="city"),
        RegexRecognizer("zip_city_state", "ZIP_CODE", [P.CITY_STATE_ZIP], 0.9, group="zip"),
        RegexRecognizer("zip_labeled", "ZIP_CODE", [P.LABELED_ZIP, P.STATE_ZIP], 0.9, group="value"),
        RegexRecognizer("age_over_89", "AGE_OVER_89", P.AGE_PATTERNS, 0.0, group="value",
                        scorer=lambda v: 0.9 if 90 <= int(v) <= 130 else None),
        RegexRecognizer("dates", "DATE", P.DATE_PATTERNS, 0.85),
        RegexRecognizer("dates_no_year", "DATE", [P.DATE_NO_YEAR], 0.8, group="value"),
        RegexRecognizer("us_phone", "PHONE_NUMBER", [P.PHONE], 0.7),
        RegexRecognizer("full_url", "URL", [P.URL], 0.9),
        NameGazetteerRecognizer(first_names, last_names),
    ]
    for i, entry in enumerate(cfg.get("custom_patterns") or []):
        recs.append(RegexRecognizer(f"custom_{entry.get('name', i)}", entry["entity"], [entry["regex"]],
                                    float(entry.get("score", 0.9))))
    return recs
