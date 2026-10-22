"""Context-risk checks (Safe Harbor's "no actual knowledge" condition).

Scrubbed text can still point to one person: a rare condition plus a place,
"our only pediatric surgeon", a small-town reference, a news-making event, or
"Dr. X's daughter". These checks run on the *scrubbed* output and flag the
file for human review instead of letting it pass. Keyword lists are in
config (context_risk:) and extend the defaults below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .patterns import STATE_NAMES

DEFAULT_ROLES = [
    "CEO", "CFO", "COO", "CTO", "CMO", "president", "vice president", "founder", "co-founder", "owner",
    "medical director", "chief of staff", "chief medical officer", "head nurse", "charge nurse", "practice manager",
    "office manager", "administrator", "superintendent", "principal", "mayor", "governor", "senator",
    "congressman", "congresswoman", "sheriff", "police chief", "fire chief", "pastor", "bishop", "rabbi", "imam",
    "coach", "head coach", "chairman", "chairwoman", "board chair",
]
DEFAULT_RARE = [
    "Huntington's disease", "ALS", "amyotrophic lateral sclerosis", "cystic fibrosis", "progeria",
    "Fabry disease", "Gaucher disease", "Pompe disease", "Niemann-Pick", "Ehlers-Danlos", "Moebius syndrome",
    "fibrodysplasia ossificans progressiva", "Creutzfeldt-Jakob", "Tay-Sachs", "Batten disease", "Rett syndrome",
    "Prader-Willi", "Wilson's disease", "Duchenne", "spinal muscular atrophy", "SMA type 1", "sickle cell",
    "hemophilia", "Marfan syndrome", "neurofibromatosis", "osteogenesis imperfecta", "Stevens-Johnson",
    "necrotizing fasciitis", "conjoined twins", "quintuplets", "sextuplets", "septuplets", "octuplets",
    "face transplant", "hand transplant", "uterus transplant", "Ebola", "rabies", "leprosy", "Hansen's disease",
    "plague", "anthrax", "measles outbreak",
]
DEFAULT_EVENTS = [
    "plane crash", "train derailment", "mass shooting", "school shooting", "shark attack", "bear attack",
    "struck by lightning", "won the lottery", "lottery winner", "house fire", "explosion at", "tornado hit",
    "on the news", "news report", "front page", "went viral", "viral video", "on TV", "celebrity", "famous",
    "Olympian", "Olympic", "NFL", "NBA", "MLB", "NHL", "Medal of Honor", "Nobel",
]
RELATIVES = ("daughter|son|wife|husband|mother|father|mom|dad|brother|sister|twin|child|kid|kids|children|"
             "spouse|partner|fiancee?|grandson|granddaughter|grandmother|grandfather|niece|nephew|aunt|uncle|"
             "cousin|stepson|stepdaughter|girlfriend|boyfriend|ex-wife|ex-husband|in-law")


@dataclass
class Risk:
    rule: str
    start: int
    end: int


def _alts(terms) -> str:
    return "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in sorted({t for t in terms if t}, key=len, reverse=True))


class ContextRisk:
    def __init__(self, cfg: dict):
        c = cfg.get("context_risk") or {}
        self.disabled = set(c.get("disabled_rules") or [])
        roles = DEFAULT_ROLES + list(c.get("unique_roles") or [])
        rare = DEFAULT_RARE + list(c.get("rare_conditions") or [])
        events = DEFAULT_EVENTS + list(c.get("distinctive_events") or [])
        towns = list(c.get("small_towns") or [])
        rel_terms = list(c.get("relationship_terms") or [])
        self.unique = re.compile(
            rf"(?i)\b(?:the|our|their|his|her|my)\s+(?:only|sole|lone|one\s+and\s+only|first\s+ever|last\s+remaining)"
            rf"\s+(?:[a-z-]+\s+){{0,3}}?(?:doctor|physician|surgeon|nurse|dentist|pharmacist|therapist|midwife|"
            rf"pediatrician|cardiologist|oncologist|specialist|provider|employee|patient|resident|veteran|teacher|"
            rf"pastor|priest|lawyer|vet|veterinarian|officer|pilot|owner|member|clinic|hospital|pharmacy|family)\b")
        self.role = re.compile(rf"(?i)\b(?:the|our|their)\s+(?:{_alts(roles)})\b")
        self.rare = re.compile(rf"(?i)\b(?:{_alts(rare)})(?![a-z])")
        self.events = re.compile(rf"(?i)\b(?:{_alts(events)})\b")
        self.small_town = re.compile(
            r"(?i)\b(?:small|tiny|little|rural)\s+(?:town|village|community|island)\b|"
            r"\bonly\s+(?:clinic|hospital|pharmacy|doctor|dentist|school|church)\s+(?:in|for)\s+"
            r"(?:town|the\s+county|the\s+area|the\s+valley|miles)\b"
            + (rf"|\b(?:{_alts(towns)})\b" if towns else ""))
        self.relationship = re.compile(
            rf"(?i)(?:\b(?:dr|mr|mrs|ms|prof)\.?\s+\w+|\b(?:person|org)_\d{{4,}}|\bthe\s+(?:owner|ceo|doctor|nurse|"
            rf"manager|director|mayor|pastor|sheriff|principal|boss|founder))'s\s+(?:{RELATIVES})\b"
            + (rf"|\b(?:{_alts(rel_terms)})\b" if rel_terms else ""))
        self.place = re.compile(
            rf"(?i)\b(?:LOCATION|ADDRESS)_\d{{4,}}\b|(?<!\d)\d{{3}}XX\b|\b(?:{STATE_NAMES})\b|"
            r"\b(?:town|county|village|city\s+of|township|parish|borough)\b"
            + (rf"|\b(?:{_alts(towns)})\b" if towns else ""))

    def check(self, text: str) -> list[Risk]:
        if not text:
            return []
        out = self._check(text)
        return [r for r in out if r.rule not in self.disabled]

    def _check(self, text: str) -> list[Risk]:
        out: list[Risk] = []
        for rule, rx in (("unique role or title", self.unique), ("unique role or title", self.role),
                         ("small-town reference", self.small_town), ("distinctive event", self.events),
                         ("relationship reference", self.relationship)):
            out += [Risk(rule, m.start(), m.end()) for m in rx.finditer(text)]
        rare = list(self.rare.finditer(text))
        if rare and self.place.search(text):
            out += [Risk("rare condition with a location", m.start(), m.end()) for m in rare]
        return out
