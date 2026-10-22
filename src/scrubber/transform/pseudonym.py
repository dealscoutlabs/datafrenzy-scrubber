"""Consistent pseudonyms: the same real entity always gets the same token.

The lookup key is HMAC-SHA256(secret, entity_type + normalized value). Only the
HMAC is stored (in /state); the token's number is assigned the first time the
key is seen. Without the secret, tokens cannot be linked back to values.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata

from ..identifiers import TOKEN_PREFIX
from ..state import Store

HONORIFICS = {"dr", "mr", "mrs", "ms", "miss", "mx", "prof", "rev", "sir", "dame", "hon", "fr", "sr", "sra", "srta"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "md", "do", "phd", "rn", "np", "pa", "dds", "dmd", "esq", "mba",
            "lpn", "cna", "pharmd", "facs", "faap", "dvm", "lcsw", "pt", "dpt", "od", "cpa"}
ORG_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "plc", "lp", "llp", "pc", "pllc", "the"}

TOKEN_RE = re.compile(r"\b(?:%s)_\d{4,}\b" % "|".join(sorted(set(TOKEN_PREFIX.values()), key=len, reverse=True)),
                      re.IGNORECASE)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def normalize_person(text: str) -> str:
    text = _fold(text).strip()
    if "," in text:
        last, _, rest = text.partition(",")
        rest_words = re.findall(r"[a-z']+", rest)
        if rest_words and not all(w.strip(".") in SUFFIXES for w in rest_words):
            text = f"{rest} {last}"
    words = [w.strip(".'") for w in re.findall(r"[a-z][a-z.'-]*", text)]
    words = [w for w in words if w and w not in HONORIFICS and w not in SUFFIXES and len(w) > 1]
    return " ".join(words)


def normalize(etype: str, text: str) -> str:
    if etype == "PERSON":
        return normalize_person(text)
    if etype in ("PHONE_NUMBER", "FAX_NUMBER"):
        main = re.split(r"(?i)\s*(?:x|ext\.?|extension)\s*\d+\s*$", text)[0]
        digits = re.sub(r"\D", "", main)
        if digits.startswith("001") and len(digits) == 13:
            digits = digits[3:]
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits or _fold(text)
    if etype == "EMAIL_ADDRESS":
        return text.strip().strip("<>").lower()
    if etype == "URL":
        t = re.sub(r"^[a-z]+://", "", text.strip().lower())
        return re.sub(r"^www\.", "", t).rstrip("/")
    if etype == "ORGANIZATION":
        words = re.findall(r"[a-z0-9&]+", _fold(text))
        return " ".join(w for w in words if w not in ORG_SUFFIXES)
    if etype in ("LOCATION", "STREET_ADDRESS"):
        return " ".join(re.findall(r"[a-z0-9]+", _fold(text)))
    if etype == "IP_ADDRESS":
        return text.strip().lower()
    # ID-like values: letters and digits only, upper case.
    return re.sub(r"[^A-Z0-9]", "", text.upper()) or _fold(text)


class Pseudonymizer:
    def __init__(self, key: bytes, store: Store, cfg: dict):
        self.key = key
        self.store = store
        self.width = int(cfg["pseudonym"]["token_width"])
        self.email_domain = cfg["pseudonym"]["email_domain"]
        self._cache: dict[str, str] = {}
        # single name word -> full normalized name, when unambiguous (from harvesting)
        self.person_alias: dict[str, str] = {}

    def set_person_aliases(self, full_names: list[str]) -> None:
        owners: dict[str, set[str]] = {}
        for full in full_names:
            parts = full.split()
            if len(parts) < 2:
                continue
            for part in parts:
                owners.setdefault(part, set()).add(full)
        self.person_alias = {part: next(iter(names)) for part, names in owners.items() if len(names) == 1}

    def hkey(self, etype: str, norm: str) -> str:
        return hmac.new(self.key, f"{etype}\x00{norm}".encode(), hashlib.sha256).hexdigest()

    def token(self, etype: str, text: str) -> str:
        norm = normalize(etype, text)
        if etype == "PERSON" and " " not in norm and norm in self.person_alias:
            norm = self.person_alias[norm]
        if not norm:
            norm = text
        cache_key = f"{etype}\x00{norm}"
        tok = self._cache.get(cache_key)
        if tok is None:
            seq = self.store.pseudonym_seq(self.hkey(etype, norm), etype)
            tok = f"{TOKEN_PREFIX[etype]}_{seq:0{self.width}d}"
            if len(self._cache) > 500_000:
                self._cache.clear()
            self._cache[cache_key] = tok
        return tok

    def email_address(self, addr: str) -> str:
        """A syntactically valid pseudonymous address for email headers."""
        return f"{self.token('EMAIL_ADDRESS', addr).lower()}@{self.email_domain}"

    def opaque_id(self, value: str, length: int = 20) -> str:
        """Deterministic opaque value (Message-IDs, Content-IDs) - not stored anywhere."""
        return hmac.new(self.key, b"opaque\x00" + value.strip().encode(), hashlib.sha256).hexdigest()[:length]
