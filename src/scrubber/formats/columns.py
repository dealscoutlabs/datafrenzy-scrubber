"""Column classification for CSV/XLSX: by header first, then by content."""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter

# Checked in order; first match wins.
HEADER_RULES: list[tuple[str, str]] = [
    ("person_name", r"(holder|subscriber|insured|member|guarantor|beneficiary|policyholder|patient|contact|"
                    r"customer|client|employee|provider|physician|doctor) (full )?name$"),
    ("dob", r"\bdob\b|\bd o b\b|birth|\bbday\b"),
    ("email", r"e ?mail"),
    ("fax", r"\bfax\b|facsimile"),
    ("phone", r"phone|mobile|\bcell\b|\btel\b|telephone|contact (number|no)|home (number|no)|work (number|no)|"
              r"\bpager\b|\bsms\b|whatsapp"),
    ("ip", r"^ip$|^ip |ip addr|client ip|remote addr|ip address|ipv[46]"),
    ("url", r"\burl\b|website|web ?site|homepage|home page|\blink\b|linkedin|profile url|web address"),
    ("ssn", r"\bssn\b|social security|soc sec|\bss ?#|\bsocial\b|\bitin\b|\btin\b"),
    ("mrn", r"\bmrn\b|medical record|chart (no|num|number|id)|patient (id|no|num|number)|^pt (id|no|num)|"
            r"hospital (no|num|number|id)|\bemr\b|\behr (id|no)"),
    ("health_plan", r"member (id|no|num|number)|subscriber|\bpolicy\b|medicaid|medicare|\bmbi\b|\bhicn\b|"
                    r"insurance (id|no|num|number)|group (id|no|num|number)|plan (id|no|num|number)|beneficiary|"
                    r"payer id"),
    ("vehicle", r"\bvin\b|license plate|\bplate\b|vehicle"),
    ("device", r"serial|device|\bimei\b|mac address|\budi\b|asset tag|implant"),
    ("biometric", r"fingerprint|voiceprint|biometric|retina|iris scan|face template"),
    ("license", r"licen[cs]e|\bnpi\b|\bdea\b|certificat|passport|driver|\bupin\b|board cert"),
    ("zip", r"\bzip\b|postal|post ?code|zipcode"),
    ("person_name", r"^(account|lead|opportunity|case|record|deal) owner$|^owner( name)?$|^assigned to$|"
                    r"^(created|modified|updated|last modified) by$|^sales ?rep$|^rep( name)?$"),
    ("organization", r"company|employer|organi[sz]ation|^org( name)?$|^account( name)?$|business|practice|"
                     r"clinic|facility|hospital|pharmacy|vendor|^payer( name)?$|insurer|carrier|school|"
                     r"workplace|firm"),
    ("account", r"account|\bacct\b|\bbank\b|routing|\biban\b|card (no|num|number)|credit card|cc (no|num|number)|"
                r"invoice"),
    ("address", r"address|street|\baddr\b|line ?[12]|address ?[12]|mailing|residence|\bapt\b|suite"),
    ("city", r"\bcity\b|\btown\b|municipality|county|precinct|neighbou?rhood|borough|district|\bvillage\b"),
    ("state", r"^state$|^st$|province|state code|^region$"),
    ("country", r"country|nation"),
    ("age", r"^age\b|\bage$|age (years|yrs|at)"),
    ("date", r"date|\bdt$|time ?stamp|^time$|created|modified|updated|admit|admission|discharge|death|deceased|"
             r"visit|appointment|\bappt\b|\bdos\b|^seen|scheduled|signed|closed|opened|last (activity|contact)|"
             r"\bdue\b|expir|\bstart\b|\bend\b|onset|surgery|procedure date"),
    ("person_name", r"^(full |patient |first |last |middle |given |family |sur|maiden |display |nick)?name$|"
                    r"^(f|l|m)name$|^(first|last|middle|given|family|sur)( name)?$|"
                    r"^(patient|member|subscriber|guarantor|contact|customer|client|provider|physician|doctor|"
                    r"referring|attending|pcp|employee|user|emergency contact|spouse|parent|guardian|"
                    r"next of kin|nok|caregiver|author|sender|recipient|from|to|cc|nurse|therapist|"
                    r"responsible party|insured|policyholder|policy holder|signer|witness)( name| full name)?$|"
                    r"name of|'s name"),
    ("other_id", r"(^| )(id|identifier|uuid|guid)$|claim|\bcase\b|ticket|user ?name|login|handle|twitter|"
                 r"instagram|facebook|tiktok|employee (id|no|num|number)|badge|confirmation|reference|"
                 r"record (id|no)|external id|crm id|contact id|lead id|opportunity id|deal id|"
                 r"encounter|accession|specimen|order (id|no|num|number)|tracking|"
                 r"authorization|referral (id|no)"),
    ("notes", r"note|comment|description|summary|message|body|text|detail|remark|history|subject|reason|"
              r"complaint|memo|narrative|activity|feedback|observation|assessment|plan|instruction|"
              r"diagnosis|\bdx\b|problem|condition|symptom|allerg|medication|\bmeds?\b|\brx\b"),
]
_COMPILED = [(t, re.compile(r)) for t, r in HEADER_RULES]

# Column type -> entity type used for its pseudonym tokens.
COLUMN_ENTITY = {
    "person_name": "PERSON", "email": "EMAIL_ADDRESS", "phone": "PHONE_NUMBER", "fax": "FAX_NUMBER",
    "ssn": "US_SSN", "mrn": "MEDICAL_RECORD_NUMBER", "health_plan": "HEALTH_PLAN_ID",
    "account": "ACCOUNT_NUMBER", "license": "LICENSE_NUMBER", "vehicle": "VEHICLE_ID", "device": "DEVICE_ID",
    "url": "URL", "ip": "IP_ADDRESS", "biometric": "BIOMETRIC_ID", "other_id": "OTHER_ID",
    "organization": "ORGANIZATION", "address": "STREET_ADDRESS", "city": "LOCATION",
    "zip": "ZIP_CODE", "dob": "DATE_BIRTH", "date": "DATE", "age": "AGE_OVER_89",
}
# Detector entity -> column type, for content-based classification.
ENTITY_COLUMN = {v: k for k, v in COLUMN_ENTITY.items()}
ENTITY_COLUMN["DATE_BIRTH"] = "date"


def normalize_header(header) -> str:
    h = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(header or ""))
    return re.sub(r"[^a-z0-9#']+", " ", h.lower()).strip()


def classify_header(header) -> str | None:
    h = normalize_header(header)
    if not h:
        return None
    for ctype, rx in _COMPILED:
        if rx.search(h):
            return ctype
    return None


_NUMERIC = re.compile(r"^[-+$€£]?\s*\d{1,6}(?:[.,]\d+)?\s*%?$|^[-+$€£]?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_BOOLEAN = {"y", "n", "yes", "no", "true", "false", "t", "f", "0", "1", "x", "-", "n/a", "na", "none", "null"}


def classify_by_content(values: list, detector, ratio: float) -> str | None:
    """Look at sampled non-empty values. Returns a column type, 'numeric' (safe
    small numbers / flags), or None (free text: scrub each cell)."""
    vals = [v for v in values if v is not None and str(v).strip() != ""]
    if not vals:
        return None
    if sum(isinstance(v, (dt.date, dt.datetime)) for v in vals) >= ratio * len(vals):
        return "date"
    strs = [str(v).strip() for v in vals]
    if all(_NUMERIC.match(s) or s.lower() in _BOOLEAN for s in strs):
        return "numeric"
    kinds: Counter = Counter()
    for s, findings in zip(strs, detector.detect_many(strs)):
        covered = [f for f in findings if (f.end - f.start) >= 0.8 * len(s)]
        if covered:
            kinds[covered[0].etype] += 1
    if kinds:
        etype, n = kinds.most_common(1)[0]
        if n >= ratio * len(strs) and etype in ENTITY_COLUMN:
            return ENTITY_COLUMN[etype]
    return None
