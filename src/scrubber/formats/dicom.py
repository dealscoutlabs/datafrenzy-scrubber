"""DICOM files.

1. Burned-in text: presidio-image-redactor's DicomImageRedactorEngine OCRs the
   pixel data and boxes out identifiers - including the patient's own name/ID
   taken from the file's tags - before the tags are scrubbed.
2. Tags: every person name, date, UID, ID, address and free-text element is
   scrubbed (dates to Jan 1 of the year, ages over 89 to 090Y, UIDs to
   consistent 2.25.x UIDs so studies and series stay linked); private tags and
   overlays are removed.
3. Verification: re-read the file, check tags, re-OCR the pixels.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import re
from pathlib import Path

import numpy as np

from .base import Context, Quarantined

TAG_ENTITY = {
    "PatientID": "MEDICAL_RECORD_NUMBER", "OtherPatientIDs": "MEDICAL_RECORD_NUMBER",
    "MedicalRecordLocator": "MEDICAL_RECORD_NUMBER", "IssuerOfPatientID": "ORGANIZATION",
    "AccessionNumber": "OTHER_ID", "StudyID": "OTHER_ID", "RequestedProcedureID": "OTHER_ID",
    "ScheduledProcedureStepID": "OTHER_ID", "PerformedProcedureStepID": "OTHER_ID", "AdmissionID": "OTHER_ID",
    "ServiceEpisodeID": "OTHER_ID", "FillerOrderNumberImagingServiceRequest": "OTHER_ID",
    "PlacerOrderNumberImagingServiceRequest": "OTHER_ID", "ClinicalTrialSubjectID": "OTHER_ID",
    "InstitutionName": "ORGANIZATION", "InstitutionalDepartmentName": "ORGANIZATION",
    "InstitutionAddress": "STREET_ADDRESS", "PatientAddress": "STREET_ADDRESS",
    "PatientTelephoneNumbers": "PHONE_NUMBER", "ReferringPhysicianTelephoneNumbers": "PHONE_NUMBER",
    "PersonTelephoneNumbers": "PHONE_NUMBER", "StationName": "DEVICE_ID", "DeviceSerialNumber": "DEVICE_ID",
    "DetectorID": "DEVICE_ID", "GantryID": "DEVICE_ID", "PlateID": "DEVICE_ID", "CassetteID": "DEVICE_ID",
    "InsurancePlanIdentification": "HEALTH_PLAN_ID", "RegionOfResidence": "LOCATION",
    "CurrentPatientLocation": "LOCATION", "PatientInstitutionResidence": "LOCATION",
}
BLANK = {"PatientBirthTime", "PatientInsurancePlanCodeSequence", "PatientReligiousPreference", "MilitaryRank",
         "BranchOfService", "PatientMotherBirthName", "PatientBirthName", "OtherPatientNames",
         "OtherPatientIDsSequence", "ReferencedPatientPhotoSequence"}
TEXT_VRS = {"LO", "SH", "ST", "LT", "UT", "UC"}
METHOD = "HIPAA Safe Harbor 45 CFR 164.514(b)(2) - Data Frenzy"  # LO: max 64 characters
MAX_LEN = {"AE": 16, "SH": 16, "LO": 64, "ST": 1024, "LT": 10240, "PN": 64, "CS": 16}


def _pydicom():
    import pydicom
    return pydicom


def _uid(value: str, ctx: Context) -> str:
    from pydicom.uid import UID_dictionary
    if value in UID_dictionary:
        return value  # standard UIDs (SOP classes, transfer syntaxes) identify nothing
    h = hmac.new(ctx.pseudo.key, b"dicom-uid\x00" + value.encode(), hashlib.sha256).digest()
    return "2.25." + str(int.from_bytes(h[:15], "big"))


def _date(value: str, ctx: Context, birth: bool = False) -> str:
    m = re.match(r"(\d{4})", value or "")
    if not m:
        return ""
    year = int(m.group(1))
    ctx.tally.years.add(year)
    ctx.tally.counts["DATE_BIRTH" if birth else "DATE"] += 1
    if birth and ctx.gen.reference_year - year > 89:
        return ""  # DICOM has no way to write "90+"; the birth date is removed
    return f"{year}0101"


def _datetime(value: str, ctx: Context) -> str:
    d = _date(value, ctx)
    return d + "000000" if d else ""


def _age(value: str, ctx: Context) -> str:
    m = re.fullmatch(r"(\d{3})([DWMY])", value or "")
    if m and m.group(2) == "Y" and int(m.group(1)) >= 90:
        ctx.tally.counts["AGE_OVER_89"] += 1
        return "090Y"
    return value


def _each(value, fn):
    from pydicom.multival import MultiValue
    if isinstance(value, (list, MultiValue)):
        return [fn(str(v)) for v in value]
    return fn(str(value))


def scrub_dataset(ds, ctx: Context) -> None:
    ds.remove_private_tags()
    for elem in list(ds):
        tag = elem.tag
        if 0x6000 <= tag.group <= 0x60FF or tag.group in (0x5000,):  # overlays and curves can hold text
            del ds[tag]
            continue
        kw = elem.keyword
        vr = elem.VR
        if vr == "SQ":
            for item in elem.value:
                scrub_dataset(item, ctx)
            continue
        if elem.value in (None, "", b"") or kw == "PixelData":
            continue
        if kw in BLANK:
            elem.value = [] if vr == "SQ" else ""
            continue
        if kw in TAG_ENTITY:
            etype = TAG_ENTITY[kw]
            ctx.tally.counts[etype] += 1
            new = _each(elem.value, lambda v, e=etype: ctx.pseudo.token(e, v))
        elif vr == "PN":
            ctx.tally.counts["PERSON"] += 1
            new = _each(elem.value, lambda v: ctx.pseudo.token("PERSON", v.replace("^", " ")))
        elif vr == "DA":
            new = _each(elem.value, lambda v, b=(kw == "PatientBirthDate"): _date(v, ctx, b))
        elif vr == "DT":
            new = _each(elem.value, lambda v: _datetime(v, ctx))
        elif vr == "AS":
            new = _age(str(elem.value), ctx)
        elif vr == "UI":
            new = _each(elem.value, lambda v: _uid(v, ctx))
        elif vr == "AE":
            new = _each(elem.value, lambda v: ctx.pseudo.token("DEVICE_ID", v)[:16])
        elif vr == "UR":
            new = f"https://scrubbed.invalid/{ctx.pseudo.token('URL', str(elem.value))}"
        elif vr in TEXT_VRS:
            new = _each(elem.value, lambda v: ctx.scrubber.scrub(v, ctx.tally).text)
        else:
            continue
        if vr in MAX_LEN:
            # tokens can be longer than the names they replace; keep values valid for their VR
            new = [v[:MAX_LEN[vr]] for v in new] if isinstance(new, list) else new[:MAX_LEN[vr]]
        elem.value = new


def _render(ds) -> list[np.ndarray]:
    """8-bit renderings of each frame, for OCR/face checks."""
    arr = ds.pixel_array
    frames = arr if (getattr(ds, "NumberOfFrames", 1) or 1) > 1 else arr[None, ...]
    out = []
    for f in frames:
        f = f.astype(np.float64)
        lo, hi = np.percentile(f, 1), np.percentile(f, 99)
        f = np.clip((f - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
        out.append(f if f.ndim == 3 else np.stack([f] * 3, axis=-1))
    return out


def _blur_faces(ds, ctx: Context) -> None:
    """Photographic DICOM (dermatology, endoscopy ...) can show faces: blank them in the pixel data."""
    from .image import vision
    frames = _render(ds)
    arr = ds.pixel_array.copy()
    multi = arr.ndim >= 3 and (getattr(ds, "NumberOfFrames", 1) or 1) > 1
    changed = False
    for i, rgb in enumerate(frames):
        for x, y, w, h, _score in vision(ctx).faces.detect(rgb):
            pad_w, pad_h = int(w * 0.3), int(h * 0.3)
            sl = (slice(max(0, y - pad_h), y + h + pad_h), slice(max(0, x - pad_w), x + w + pad_w))
            target = arr[i] if multi else arr
            target[sl] = target.min()
            ctx.tally.counts["FACE_IMAGE"] += 1
            changed = True
    if changed:
        ds.PixelData = arr.tobytes()


def verify_dicom(data: bytes, ctx: Context, item: str) -> None:
    pydicom = _pydicom()
    try:
        ds = pydicom.dcmread(io.BytesIO(data))
    except Exception as exc:
        ctx.issue(f"{item}: scrubbed DICOM does not read ({type(exc).__name__})")
        return
    texts = []
    for elem in ds.iterall():
        if elem.VR == "PN" and elem.value and not re.fullmatch(r"(PERSON_\d{4,}\\?)+", str(elem.value)):
            ctx.issue(f"{item}: a person-name tag survived scrubbing")
        if elem.VR == "DA" and elem.value and not all(re.fullmatch(r"\d{4}0101", str(v)) for v in
                                                      (elem.value if isinstance(elem.value, list) else [elem.value])):
            ctx.issue(f"{item}: a date tag kept month/day")
        if elem.VR in TEXT_VRS and isinstance(elem.value, str) and elem.keyword != "DeidentificationMethod":
            texts.append(elem.value)
        if elem.tag.is_private:
            ctx.issue(f"{item}: private tags survived")
            break
    ctx.residual("\n\n".join(texts), item)
    if "PixelData" in ds:
        from PIL import Image

        from ..detect.vision import ocr_text
        try:
            for f in _render(ds):
                ctx.residual(ocr_text(Image.fromarray(f)), f"{item} pixels")
        except Exception as exc:
            ctx.issue(f"{item}: pixel data could not be re-checked ({type(exc).__name__})")


def scrub_dicom_bytes(data: bytes, ctx: Context, item: str, depth: int = 0) -> bytes:
    pydicom = _pydicom()
    try:
        ds = pydicom.dcmread(io.BytesIO(data), force=True)
    except Exception as exc:
        raise Quarantined(f"not a readable DICOM file ({type(exc).__name__})") from exc
    if "PixelData" in ds:
        from presidio_image_redactor import DicomImageRedactorEngine

        from .image import vision
        try:
            ds.decompress()  # redaction needs raw pixels; written back uncompressed
        except Exception:
            pass
        engine = DicomImageRedactorEngine(image_analyzer_engine=vision(ctx).analyzer)
        try:
            ds = engine.redact(ds, fill="contrast")  # uses the patient tags as a deny list by default
        except Exception as exc:
            raise Quarantined(f"DICOM pixel data could not be checked for burned-in text ({type(exc).__name__})") from exc
        ctx.scrubber._count(vision(ctx).analyzer.adapter.findings, ctx.tally)
        vision(ctx).analyzer.adapter.findings = []
        try:
            _blur_faces(ds, ctx)
        except Exception:
            pass
    scrub_dataset(ds, ctx)
    if getattr(ds, "file_meta", None) is not None:
        for kw in ("MediaStorageSOPInstanceUID",):
            if kw in ds.file_meta:
                ds.file_meta[kw].value = _uid(str(ds.file_meta[kw].value), ctx)
    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = METHOD
    out = io.BytesIO()
    ds.save_as(out, enforce_file_format=bool(getattr(ds, "file_meta", None)))
    result = out.getvalue()
    verify_dicom(result, ctx, item)
    ctx.tally.records += 1
    return result


def looks_like_dicom(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except OSError:
        return False


def process_dicom(src: Path, dst: Path, ctx: Context) -> None:
    out = scrub_dicom_bytes(src.read_bytes(), ctx, ctx.out_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
