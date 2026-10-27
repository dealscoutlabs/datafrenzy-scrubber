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
        # US 3-2-4, other 9-digit groupings (Canadian SIN 3-3-3), UK National Insurance (AB 12 34 56 C)
        value = (r"\d{3}[- ]?\d{2}[- ]?\d{4}|\d{3}[- ]\d{3}[- ]\d{3}|[A-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D]?|"
                 r"(?:x{3}|\*{3})[- ]?(?:x{2}|\*{2})[- ]?\d{4}")
        sep = r"\s*\**\s*(?:is\s+|of\s+|=\s*|:\s*|#\s*|-\s*)?\**\s*"
    elif value_kind == "phone":
        value = r"\+?\(?\d[\d ().-]{5,20}\d(?:[ \t]*(?:x|ext\.?)[ \t]*\d{1,6})?"
        sep = r"[ \t]*\**[ \t]*(?:is[ \t]+|=[ \t]*|:[ \t]*|#[ \t]*|-[ \t]*)\**[ \t]*[\"']?"
    elif value_kind in ("place", "name"):
        # a form field: "City of Birth: Jamesview", "First Name: Wazir", "**Customer:** Kelly"
        # a period ends the name unless it closes an initial or abbreviation ("J.", "St."):
        # "Name: Elvis Presley. Age: 17" is the name, then the next field's label
        part = r"(?:[A-Z]\.|(?:St|Ste|Jr|Sr|Mc)\.|[A-Z][A-Za-z']*[A-Za-z])"
        value = part + rf"(?:[ -](?:{part}|de|del|la|van|von|da))" + ("{0,3}" if value_kind == "name" else "{0,2}")
        sep = r"[ \t]*\**[ \t]*[:=][ \t]*\**[ \t]*[\"']?"   # same line only ("Patient:\nPhone:" is two fields)
        idword = r""
    elif value_kind == "plate":
        value = r"(?=[A-Za-z0-9 -]{0,9}\d)[A-Za-z0-9]{2,8}(?:[ -][A-Za-z0-9]{2,4})?"
        sep = r"\s*(?:is\s+|of\s+|=\s*|:\s*|#\s*|-\s*)?"
    elif value_kind == "address":
        # "Address: 76 Matthew lodge, Studio 06l": starts with a number, runs to the end of the field
        value = r"\d[\w/-]*,?[ \t]+[A-Za-z][^\n*|;]{2,60}?(?=[ \t]*(?:\*\*|\n|;|\||[ \t]-[ \t]|$|\.[ \t]|,[ \t]+[A-Z][a-z]+:))"
        sep = r"[ \t]*\**[ \t]*(?:is[ \t]+|=[ \t]*|:[ \t]*)\**[ \t]*[\"']?"
    elif value_kind == "handle":
        value = r"@?[A-Za-z0-9][A-Za-z0-9._-]{2,40}(?<![.-])"
        # "created by hayes96": a "... by" label needs no colon
        by = r"|(?<=by)\s+|(?<=to)\s+" if any(lab.rstrip(")'").endswith(("by", "to")) for lab in labels) else ""
        sep = rf"\s*[\"']?\s*\**\s*(?:(?:is|was)\s+|[:=#>]\s*{by})\**\s*[\"']?(?=\S)"
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
            rf"\b{P.HONORIFIC}\.?[ \t]+(?P<value>[A-Z][a-zA-Z'-]+(?:[ \t]+[A-Z]\.?)?(?:[ \t]+[A-Z][a-zA-Z'-]+){{0,2}})")
        self.greeting = re.compile(rf"{P.GREETING}(?P<value>[A-Z][a-z'-]+(?:[ \t]+[A-Z][a-z'-]+)?)\b", re.M)
        self.last_first = re.compile(r"\b(?P<last>[A-Z][a-z'-]+),[ \t]+(?P<first>[A-Z][a-z'-]+)\b(?![ \t]*,?[ \t]*[A-Z]{2}\b)")
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
        for m in self.last_first.finditer(text):  # "Wood, Natasha"
            last, first = m.group("last"), m.group("first")
            if last.upper() in self.last and first.upper() in self.first:
                out.append(self._result(m.start(), m.end(), 0.75))
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


class CapsRowRecognizer(EntityRecognizer):
    """ALL-CAPS runs on one line, as payroll and tax forms print them ("QUINTERO MARISOL ANNE R",
    "LAKESHORE CAMPUSES OF OHIO"). A run is a person when it holds a known first name, or
    is a surname followed by an initial; an organization when it holds a word like CAMPUSES,
    HOSPITAL or INC. Runs with label words ("VT CHILD CARE CONTRIB EMPLOYER") are left alone."""

    WORD = r"(?:PERSON_\d{4,}|[A-Z][A-Z'.-]*)"
    RUN = re.compile(rf"(?<![A-Za-z_])(?:{WORD})(?:[ \t]+(?:{WORD}))+(?![A-Za-z_\d])")
    TOKEN = re.compile(r"PERSON_\d{4,}")
    ORG_WORDS = {"CAMPUS", "CAMPUSES", "HOSPITAL", "HOSPITALS", "CENTER", "CENTERS", "CENTRE", "CLINIC", "CLINICS",
                 "HEALTH", "HEALTHCARE", "MEDICAL", "RECOVERY", "TREATMENT", "BEHAVIORAL", "REHAB",
                 "REHABILITATION", "INC", "LLC", "LLP", "CORP", "CORPORATION", "COMPANY", "ASSOCIATES",
                 "PARTNERS", "FOUNDATION", "INSTITUTE", "UNIVERSITY", "PHARMACY", "LABS", "LABORATORIES",
                 "NURSING", "HOSPICE", "WELLNESS", "PRACTICE", "GROUP", "SYSTEMS", "SOLUTIONS", "ACADEMY"}

    def __init__(self, first_names: frozenset[str], last_names: frozenset[str]):
        self.first = first_names
        self.last = last_names
        super().__init__(supported_entities=["PERSON", "ORGANIZATION"], name="caps_row", supported_language="en")

    def load(self) -> None:
        pass

    def _person(self, words: list[str]) -> bool:
        alpha = [w.strip(".'-") for w in words]
        if len(alpha) > 5 or any(w.lower() in P.LABEL_WORDS and len(w) > 1 for w in alpha):
            return False
        names = [w for w in alpha if not self.TOKEN.fullmatch(w)]
        if names and len(names) < len(alpha):
            # caps words next to an already-scrubbed name are the rest of that name ("CALLOWAY PERSON_1342")
            return True
        if any(len(w) > 2 and w in self.first and w.lower() not in P.COMMON_WORD_NAMES for w in alpha):
            return True
        # "CALLOWAY QUIL R": surname, a (cut-off) first name, a middle initial
        return 2 <= len(alpha) <= 4 and len(alpha[-1]) == 1 and alpha[0] in self.last and len(alpha[0]) > 2

    def analyze(self, text, entities, nlp_artifacts=None):
        out = []
        for m in self.RUN.finditer(text):
            words = m.group(0).split()
            # a word ending a sentence ("NORMAL.  MANUAL.") ends the run: only initials keep a period
            cut = next((k for k, w in enumerate(words[:-1]) if w.endswith(".") and len(w.strip(".")) > 1), None)
            end = m.end()
            if cut is not None:
                words = words[:cut + 1]
                end = m.start() + m.group(0).index(words[-1], len(" ".join(words[:-1]))) + len(words[-1]) - 1
                if len(words) < 2:
                    continue
            if all(self.TOKEN.fullmatch(w) for w in words):
                continue
            if len(words) >= 2 and any(w.strip(".,") in self.ORG_WORDS for w in words):
                etype, score = "ORGANIZATION", 0.75
            elif self._person(words):
                etype, score = "PERSON", 0.7
            else:
                continue
            out.append(RecognizerResult(etype, m.start(), end, score,
                                        recognition_metadata={"recognizer_name": self.name}))
        return out


def _firm_score(last_names):
    """"Casey, Gray and Duncan" / "Lopez and Sons": every name word is a surname."""
    filler = {"Sons", "Daughters", "Brothers", "Partners", "Associates", "Company", "Co"}

    def score(v: str) -> float | None:
        words = [w for w in re.findall(r"[A-Z][a-z]+", v)]
        names = [w for w in words if w not in filler]
        if len(words) < 2 or not names or not all(w.upper() in last_names for w in names):
            return None
        return 0.8
    return score


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
        RegexRecognizer("town_prefix", "LOCATION", [P.PLACE_PREFIX], 0.65),
        RegexRecognizer("city_code_zip", "LOCATION", [P.CITY_CODE_ZIP], 0.75, group="city"),
        RegexRecognizer("zip_city_code", "ZIP_CODE", [P.CITY_CODE_ZIP], 0.9, group="zip"),
        RegexRecognizer("firm_names", "ORGANIZATION", [P.FIRM_NAMES], 0.0, scorer=_firm_score(last_names)),
        RegexRecognizer("firm_suffix", "ORGANIZATION", [P.FIRM_SUFFIX], 0.75),
        RegexRecognizer("hyphen_surnames", "PERSON", [P.HYPHEN_SURNAMES], 0.0, scorer=lambda v: 0.7 if all(
            part.upper() in last_names for part in v.split("-")) else None),
        RegexRecognizer("town_suffix", "LOCATION", [P.PLACE_SUFFIX], 0.65, group="value"),
        RegexRecognizer("zip_city_state", "ZIP_CODE", [P.CITY_STATE_ZIP], 0.9, group="zip"),
        RegexRecognizer("zip_labeled", "ZIP_CODE", [P.LABELED_ZIP, P.STATE_ZIP], 0.9, group="value"),
        RegexRecognizer("uk_postcode", "ZIP_CODE", [P.UK_POSTCODE], 0.7),
        RegexRecognizer("coordinates", "LOCATION", [P.COORDINATES], 0.85),
        RegexRecognizer("card_groups", "ACCOUNT_NUMBER", [P.CARD_GROUPS], 0.75),
        RegexRecognizer("very_long_number", "OTHER_ID", [P.VERY_LONG_DIGITS], 0.65),
        RegexRecognizer("username_shape", "OTHER_ID", [P.USERNAME_SHAPE], 0.6),
        RegexRecognizer("short_coded_id", "OTHER_ID", [P.SHORT_CODED_ID], 0.6),
        RegexRecognizer("intl_phone", "PHONE_NUMBER", [P.INTL_PHONE, P.INTL_PHONE2], 0.7),
        RegexRecognizer("ca_postcode", "ZIP_CODE", [P.CA_POSTCODE], 0.75),
        RegexRecognizer("nino", "US_SSN", [P.NINO], 0.75),
        RegexRecognizer("digits_333", "OTHER_ID", [P.DIGITS_333], 0.6),
        RegexRecognizer("dashed_id", "OTHER_ID", [P.DASHED_ID], 0.6),
        RegexRecognizer("username_digits", "OTHER_ID", [P.USERNAME_DIGITS], 0.55),
        RegexRecognizer("age_over_89", "AGE_OVER_89", P.AGE_PATTERNS, 0.0, group="value",
                        scorer=lambda v: 0.9 if 90 <= int(v) <= 130 else None),
        RegexRecognizer("dates", "DATE", P.DATE_PATTERNS, 0.85),
        RegexRecognizer("dates_no_year", "DATE", [P.DATE_NO_YEAR], 0.8, group="value"),
        RegexRecognizer("us_phone", "PHONE_NUMBER", [P.PHONE], 0.7),
        RegexRecognizer("full_url", "URL", [P.URL], 0.9),
        RegexRecognizer("bare_domain", "URL", [P.BARE_DOMAIN], 0.6),
        RegexRecognizer("spoken_phone", "PHONE_NUMBER", [P.SPOKEN_DIGITS], 0.0,
                        scorer=lambda v: 0.65 if len(re.sub(r"\D", "", v)) in (7, 9, 10, 11) else None),
        RegexRecognizer("spoken_number", "OTHER_ID", [P.SPOKEN_DIGITS], 0.0,
                        scorer=lambda v: 0.6 if len(re.sub(r"\D", "", v)) not in (7, 9, 10, 11) else None),
        NameGazetteerRecognizer(first_names, last_names),
        CapsRowRecognizer(first_names, last_names),
    ]
    for i, entry in enumerate(cfg.get("custom_patterns") or []):
        recs.append(RegexRecognizer(f"custom_{entry.get('name', i)}", entry["entity"], [entry["regex"]],
                                    float(entry.get("score", 0.9))))
    return recs
