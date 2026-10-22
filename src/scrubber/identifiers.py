"""The 18 HIPAA Safe Harbor identifiers (45 CFR 164.514(b)(2)) and the internal
entity types the detectors emit for each one.

Every detector finding carries one internal entity type. The audit report
counts removals per Safe Harbor category using SAFE_HARBOR below.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    number: int
    title: str
    entities: tuple[str, ...]


SAFE_HARBOR: tuple[Category, ...] = (
    Category(1, "Names", ("PERSON", "ORGANIZATION")),
    Category(2, "Geographic subdivisions smaller than a state", ("STREET_ADDRESS", "LOCATION", "ZIP_CODE")),
    Category(3, "Dates (except year) and ages over 89", ("DATE", "DATE_BIRTH", "AGE_OVER_89")),
    Category(4, "Telephone numbers", ("PHONE_NUMBER",)),
    Category(5, "Fax numbers", ("FAX_NUMBER",)),
    Category(6, "Email addresses", ("EMAIL_ADDRESS",)),
    Category(7, "Social Security numbers", ("US_SSN",)),
    Category(8, "Medical record numbers", ("MEDICAL_RECORD_NUMBER",)),
    Category(9, "Health plan beneficiary numbers", ("HEALTH_PLAN_ID",)),
    Category(10, "Account numbers", ("ACCOUNT_NUMBER",)),
    Category(11, "Certificate/license numbers", ("LICENSE_NUMBER",)),
    Category(12, "Vehicle identifiers and serial numbers", ("VEHICLE_ID",)),
    Category(13, "Device identifiers and serial numbers", ("DEVICE_ID",)),
    Category(14, "URLs", ("URL",)),
    Category(15, "IP addresses", ("IP_ADDRESS",)),
    Category(16, "Biometric identifiers", ("BIOMETRIC_ID", "MEDIA_BIOMETRIC")),
    Category(17, "Full-face photographs and comparable images", ("FACE_IMAGE",)),
    Category(18, "Any other unique identifying number, characteristic, or code", ("OTHER_ID",)),
)

ENTITY_TO_CATEGORY: dict[str, int] = {e: c.number for c in SAFE_HARBOR for e in c.entities}

# Token prefix used when an entity is pseudonymized (PERSON_0412, ORG_0031 ...).
# Entities missing here are generalized rather than tokenized.
TOKEN_PREFIX: dict[str, str] = {
    "PERSON": "PERSON",
    "ORGANIZATION": "ORG",
    "STREET_ADDRESS": "ADDRESS",
    "LOCATION": "LOCATION",
    "PHONE_NUMBER": "PHONE",
    "FAX_NUMBER": "FAX",
    "EMAIL_ADDRESS": "EMAIL",
    "US_SSN": "SSN",
    "MEDICAL_RECORD_NUMBER": "MRN",
    "HEALTH_PLAN_ID": "HEALTHPLAN",
    "ACCOUNT_NUMBER": "ACCOUNT",
    "LICENSE_NUMBER": "LICENSE",
    "VEHICLE_ID": "VEHICLE",
    "DEVICE_ID": "DEVICE",
    "URL": "URL",
    "IP_ADDRESS": "IP",
    "BIOMETRIC_ID": "BIOMETRIC",
    "OTHER_ID": "ID",
}

GENERALIZED = {"ZIP_CODE", "DATE", "DATE_BIRTH", "AGE_OVER_89"}

TEXT_ENTITIES = tuple(TOKEN_PREFIX) + tuple(sorted(GENERALIZED))
