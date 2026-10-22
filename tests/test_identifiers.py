"""One test (at least) for each of the 18 HIPAA Safe Harbor identifiers.

Each case: text containing a synthetic identifier -> the identifier must be
gone from the scrubbed text and the expected entity type must be counted.
"""

import re

import pytest

from scrubber.identifiers import ENTITY_TO_CATEGORY, SAFE_HARBOR
from scrubber.pipeline import classify
from scrubber.transform.scrub import Tally

CASES = [
    # 1 Names (people, relatives, employers, household members)
    (1, "Patient Marisol Fenwick was seen today.", ["Marisol", "Fenwick"], "PERSON"),
    (1, "Her son Desmond Okafor drove her home.", ["Desmond", "Okafor"], "PERSON"),
    (1, "Dr. Haverford reviewed the chart.", ["Haverford"], "PERSON"),
    (1, "He works for Brightline Freight Solutions LLC as a driver.", ["Brightline Freight"], "ORGANIZATION"),
    (1, "Thanks,\nGwendolyn", ["Gwendolyn"], "PERSON"),
    # 2 Geography smaller than a state
    (2, "She lives at 4821 Willow Creek Road, Apt 12.", ["4821 Willow Creek"], "STREET_ADDRESS"),
    (2, "Mail to PO Box 7731 please.", ["Box 7731"], "STREET_ADDRESS"),
    (2, "Moved to Port Kevinberg, OR 97031 last year.", ["Kevinberg", "97031"], "ZIP_CODE"),
    (2, "Clinic in Lake Sarahmouth, TX.", ["Sarahmouth"], "LOCATION"),
    # 3 Dates (except year) and ages over 89
    (3, "Admitted on March 14, 2021 and discharged 03/19/2021.", ["March 14", "03/19"], "DATE"),
    (3, "DOB: 07/04/1958.", ["07/04"], "DATE_BIRTH"),
    (3, "Follow-up appointment on 2023-11-02T09:30:00.", ["11-02"], "DATE"),
    (3, "A 94-year-old woman with CHF.", ["94-year"], "AGE_OVER_89"),
    (3, "DOB 1/2/1929, lives alone.", ["1929", "1/2/"], "DATE_BIRTH"),
    # 4 Telephone
    (4, "Call her at (415) 555-0172 after 5.", ["555-0172"], "PHONE_NUMBER"),
    (4, "Cell: +1-212-555-0143", ["555-0143"], "PHONE_NUMBER"),
    # 5 Fax
    (5, "Fax: 415-555-0199 attn records.", ["555-0199"], "FAX_NUMBER"),
    # 6 Email
    (6, "Reach me at t.whitlock88@examplemail.com.", ["whitlock88"], "EMAIL_ADDRESS"),
    # 7 SSN
    (7, "SSN 523-71-4402 on file.", ["523-71-4402"], "US_SSN"),
    (7, "social security number: 523714402", ["523714402"], "US_SSN"),
    # 8 Medical record numbers
    (8, "MRN: 00483921 per registration.", ["00483921"], "MEDICAL_RECORD_NUMBER"),
    (8, "Medical record # A7734521", ["A7734521"], "MEDICAL_RECORD_NUMBER"),
    # 9 Health plan beneficiary numbers
    (9, "Medicare MBI 1EG4-TE5-MK73 verified.", ["1EG4-TE5-MK73"], "HEALTH_PLAN_ID"),
    (9, "Member ID: XJH448812001", ["XJH448812001"], "HEALTH_PLAN_ID"),
    (9, "Medicaid ID AB123456C", ["AB123456C"], "HEALTH_PLAN_ID"),
    # 10 Account numbers
    (10, "Account # 0048812231 is past due.", ["0048812231"], "ACCOUNT_NUMBER"),
    (10, "Card 4111 1111 1111 1111 declined.", ["4111 1111 1111 1111"], "ACCOUNT_NUMBER"),
    # 11 Certificate / license numbers
    (11, "Provider NPI 1234567893.", ["1234567893"], "LICENSE_NUMBER"),
    (11, "DEA AB1234563 on the script.", ["AB1234563"], "LICENSE_NUMBER"),
    (11, "State license # MD448210.", ["MD448210"], "LICENSE_NUMBER"),
    # 12 Vehicle identifiers
    (12, "VIN 1HGCM82633A004352 registered.", ["1HGCM82633A004352"], "VEHICLE_ID"),
    (12, "License plate 7ABC 123 seen in lot.", ["7ABC 123"], "VEHICLE_ID"),
    # 13 Device identifiers
    (13, "Pacemaker serial SN: PX99812A3.", ["PX99812A3"], "DEVICE_ID"),
    (13, "Device MAC 00:1A:2B:3C:4D:5E online.", ["00:1A:2B:3C:4D:5E"], "DEVICE_ID"),
    # 14 URLs
    (14, "See https://portal.examplehealth.org/u/88231 for results.", ["examplehealth"], "URL"),
    # 15 IP addresses
    (15, "Login from 203.0.113.77 flagged.", ["203.0.113.77"], "IP_ADDRESS"),
    # 16 Biometric identifiers
    (16, "Fingerprint ID: FP-881230 enrolled.", ["FP-881230"], "BIOMETRIC_ID"),
    (16, "Voiceprint # VP44120987 stored.", ["VP44120987"], "BIOMETRIC_ID"),
    # 18 Any other unique identifying number or code
    (18, "Claim # CLM-2021-88812 denied.", ["CLM-2021-88812"], "OTHER_ID"),
    (18, "Case 2023-45120 escalated.", ["2023-45120"], "OTHER_ID"),
    (18, "Portal username: mfenwick88", ["mfenwick88"], "OTHER_ID"),
    (18, "Follow @marisol_f for updates.", ["marisol_f"], "OTHER_ID"),
    (18, "Ref A7X92KQ3BB on the order.", ["A7X92KQ3BB"], "OTHER_ID"),
]


@pytest.mark.parametrize("category,text,secrets,etype", CASES, ids=[f"{c[0]}-{c[3]}-{i}" for i, c in enumerate(CASES)])
def test_identifier_removed(scrubber, category, text, secrets, etype):
    tally = Tally()
    out = scrubber.scrub(text, tally).text
    for s in secrets:
        assert s not in out, f"{s!r} leaked: {out!r}"
    assert tally.counts[etype] >= 1, f"expected {etype}, got {dict(tally.counts)} -> {out!r}"
    assert ENTITY_TO_CATEGORY[etype] == category


def test_every_category_has_a_text_case_or_handler():
    covered = {c[0] for c in CASES}
    # 17 (faces) is an image check (see test_17_*); 16 also covers audio/video quarantine.
    assert covered | {17} == {c.number for c in SAFE_HARBOR}


@pytest.mark.parametrize("name", ["visit.jpg", "scan.png", "photo.HEIC", "xray.tiff", "ct.dcm"])
def test_17_images_are_handled_as_images(name, tmp_path):
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff" + bytes(32))
    kind, reason = classify(p)
    assert kind in ("image", "dicom") and reason is None


def test_17_full_face_photos_blurred(ctx):
    import io

    import numpy as np
    from PIL import Image

    from fixtures.synth_images import drawn_face, save
    from scrubber.detect.vision import FaceDetector
    from scrubber.formats.image import scrub_image_bytes
    out = Image.open(io.BytesIO(scrub_image_bytes(save(drawn_face(500), "JPEG"), ctx, "face.jpg")))
    assert FaceDetector(0.6).detect(np.array(out.convert("RGB")), 0.2) == []
    assert ctx.tally.counts["FACE_IMAGE"] == 1


@pytest.mark.parametrize("name", ["voicemail.wav", "call.mp3", "visit.m4a", "telehealth.mp4"])
def test_16_media_quarantined_as_biometric(name, tmp_path):
    p = tmp_path / name
    p.write_bytes(bytes(32))
    kind, reason = classify(p)
    assert kind == "media" and "voiceprint" in reason


def test_allowlisted_public_companies_kept(scrubber):
    out = scrubber.scrub("Claims go to UnitedHealthcare; files are on Microsoft servers.").text
    assert "UnitedHealthcare" in out and "Microsoft" in out


def test_large_places_and_years_kept(scrubber):
    out = scrubber.scrub("She moved from Ohio to California in 2019.").text
    assert out == "She moved from Ohio to California in 2019."


def test_clinical_text_stays_readable(scrubber):
    text = "Take 1/2 tablet twice daily. BP 120/80, HbA1c 7.2. COVID-19 negative."
    assert scrubber.scrub(text).text == text


def test_custom_regex_and_deny_list_from_config(cfg, store):
    from scrubber.config import load_config, load_zip3_restricted
    from scrubber.detect.engine import Detector
    from scrubber.transform.generalize import Generalizer
    from scrubber.transform.pseudonym import Pseudonymizer
    from scrubber.transform.scrub import TextScrubber

    c = load_config(overrides={"reference_year": 2026,
                               "custom_patterns": [{"name": "pt", "entity": "MEDICAL_RECORD_NUMBER",
                                                    "regex": r"\bPT~\d{5}\b", "score": 0.95}],
                               "deny_list": {"ORGANIZATION": ["Sunny Acres"]},
                               "allowlist": {"organizations": ["Brightline Freight Solutions LLC"]}})
    sc = TextScrubber(Detector(c), Pseudonymizer(b"\x02" * 32, store, c), Generalizer(2026, load_zip3_restricted()))
    out = sc.scrub("Resident PT~12345 at Sunny Acres; employer Brightline Freight Solutions LLC.").text
    assert "PT~12345" not in out and re.search(r"MRN_\d{4}", out)
    assert "Sunny Acres" not in out
    assert "Brightline Freight Solutions LLC" in out


def test_thresholds_configurable(store):
    from scrubber.config import load_config
    from scrubber.detect.engine import Detector

    strict = Detector(load_config(overrides={"reference_year": 2026, "thresholds": {"OTHER_ID": 0.99}}))
    assert not [f for f in strict.detect("Ref A7X92KQ3BB on the order.") if f.etype == "OTHER_ID"]
