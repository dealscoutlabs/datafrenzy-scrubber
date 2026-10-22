import io
import re

import numpy as np
import pydicom
import pytesseract
from PIL import Image

from fixtures import synth_images as si
from scrubber.formats.dicom import scrub_dicom_bytes


def _scrub(synth, ctx):
    p, doc = synth.person(), synth.person()
    src = si.dicom_bytes(synth, p, doc)
    out = scrub_dicom_bytes(src, ctx, "chest.dcm")
    return p, doc, pydicom.dcmread(io.BytesIO(src)), pydicom.dcmread(io.BytesIO(out)), out


def test_tags_scrubbed(synth, ctx):
    p, doc, a, b, raw = _scrub(synth, ctx)
    assert re.fullmatch(r"PERSON_\d{4}", str(b.PatientName))
    assert re.fullmatch(r"MRN_\d{4}", b.PatientID)
    assert b.PatientBirthDate == ""          # born 1931: reveals age over 89
    assert b.PatientAge == "090Y"
    assert b.StudyDate == "20220101" and b.SeriesDate == "20220101"
    assert re.fullmatch(r"PERSON_\d{4}", str(b.ReferringPhysicianName))
    assert "PERSON_" in b.StudyDescription and p.last not in b.StudyDescription
    assert re.fullmatch(r"ORG_\d{4}", b.InstitutionName)
    assert re.fullmatch(r"DEVICE_\d{4}", b.DeviceSerialNumber)
    assert not any(e.tag.is_private for e in b)
    assert b.PatientIdentityRemoved == "YES"
    for s in (p.last, p.first, p.mrn, p.phone, doc.last, p.street):
        assert s.encode() not in raw, s
    assert ctx.tally.issues == []


def test_uids_replaced_consistently(synth, ctx):
    p, doc, a, b, _ = _scrub(synth, ctx)
    assert b.StudyInstanceUID != a.StudyInstanceUID and b.StudyInstanceUID.startswith("2.25.")
    assert b.SOPInstanceUID == b.file_meta.MediaStorageSOPInstanceUID
    assert b.SOPClassUID == a.SOPClassUID  # standard UID kept
    # another image of the same study keeps the same (new) study UID
    buf = io.BytesIO()
    a.save_as(buf, enforce_file_format=True)
    again = pydicom.dcmread(io.BytesIO(scrub_dicom_bytes(buf.getvalue(), ctx, "x.dcm")))
    assert again.StudyInstanceUID == b.StudyInstanceUID


def test_burned_in_text_removed(synth, ctx):
    p, doc, a, b, _ = _scrub(synth, ctx)
    before = pytesseract.image_to_string(Image.fromarray(a.pixel_array))
    assert p.last.upper() in before.replace(" ", "")
    after = pytesseract.image_to_string(Image.fromarray(b.pixel_array))
    assert p.last.upper() not in after and p.mrn not in after
    assert b.pixel_array.shape == a.pixel_array.shape
    assert np.array_equal(b.pixel_array[400:600, 300:500], a.pixel_array[400:600, 300:500])  # anatomy untouched
