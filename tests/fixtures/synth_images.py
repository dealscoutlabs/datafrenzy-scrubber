"""Synthetic images, scans and DICOM. Text comes from Faker identities; faces
are drawn (no photo of any real person is used)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .synth import Person, Synth


def font(size: int = 36):
    return ImageFont.load_default(size=size)


def text_image(lines: list[str], width: int = 1600, size: int = 40, bg=(255, 255, 255)) -> Image.Image:
    line_h = int(size * 1.6)
    img = Image.new("RGB", (width, line_h * len(lines) + 60), bg)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((40, 30 + i * line_h), line, fill=(0, 0, 0), font=font(size))
    return img


def drawn_face(size: int = 400, skin=(224, 172, 140)) -> Image.Image:
    im = Image.new("RGB", (size, size), (90, 120, 160))
    d = ImageDraw.Draw(im)
    s = size / 400
    d.ellipse([110 * s, 40 * s, 290 * s, 150 * s], fill=(60, 40, 25))
    d.ellipse([120 * s, 70 * s, 280 * s, 300 * s], fill=skin)
    d.rectangle([175 * s, 290 * s, 225 * s, 360 * s], fill=skin)
    for cx in (165, 235):
        d.ellipse([(cx - 22) * s, 160 * s, (cx + 22) * s, 182 * s], fill=(250, 250, 250))
        d.ellipse([(cx - 9) * s, 162 * s, (cx + 9) * s, 180 * s], fill=(70, 50, 30))
        d.ellipse([(cx - 4) * s, 167 * s, (cx + 4) * s, 175 * s], fill=(10, 10, 10))
        d.line([(cx - 25) * s, 148 * s, (cx + 22) * s, 144 * s], fill=(50, 35, 20), width=int(6 * s))
    d.polygon([(200 * s, 180 * s), (188 * s, 228 * s), (212 * s, 228 * s)], fill=(200, 145, 115))
    d.ellipse([170 * s, 245 * s, 230 * s, 265 * s], fill=(170, 70, 70))
    return im.filter(ImageFilter.GaussianBlur(2 * s))


def id_badge(p: Person) -> Image.Image:
    """A staff badge: drawn face plus name, ID number and phone."""
    card = Image.new("RGB", (1400, 640), (255, 255, 255))
    card.paste(drawn_face(420), (40, 110))
    d = ImageDraw.Draw(card)
    for i, line in enumerate([p.name, f"MRN {p.mrn}", f"Phone {p.phone}", p.email]):
        d.text((520, 150 + i * 90), line, fill=(0, 0, 0), font=font(46))
    return card


def note_lines(s: Synth, p: Person) -> list[str]:
    return [f"Patient: {p.name}", f"DOB: {s.date_text(p.dob)}   MRN: {p.mrn}", f"Phone: {p.phone}",
            f"SSN: {p.ssn}", f"Email: {p.email}"]


def jpeg_with_gps(img: Image.Image) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "SynthCam"            # Make
    exif[0x0110] = "Model 7"             # Model
    exif[0x0132] = "2022:05:17 09:30:00"  # DateTime
    exif[0x013B] = "Photographer Name"   # Artist
    gps = exif.get_ifd(0x8825)
    gps.update({1: "N", 2: (40.0, 26.0, 46.0), 3: "W", 4: (79.0, 58.0, 56.0)})
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif, quality=92)
    return out.getvalue()


def save(img: Image.Image, fmt: str, **kw) -> bytes:
    out = io.BytesIO()
    img.save(out, fmt, **kw)
    return out.getvalue()


def tiff_pages(pages: list[Image.Image]) -> bytes:
    out = io.BytesIO()
    pages[0].save(out, "TIFF", save_all=True, append_images=pages[1:], compression="tiff_lzw")
    return out.getvalue()


def heic(img: Image.Image) -> bytes:
    import pillow_heif
    pillow_heif.register_heif_opener()
    out = io.BytesIO()
    img.save(out, "HEIF", quality=90)
    return out.getvalue()


def scanned_pdf(pages: list[Image.Image]) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    for img in pages:
        page = doc.new_page(width=612, height=612 * img.height / img.width)
        page.insert_image(page.rect, stream=save(img, "PNG"))
    doc.set_metadata({"author": "Scanner Operator", "creationDate": "D:20220517093000"})
    return doc.tobytes()


def dicom_bytes(s: Synth, p: Person, doctor: Person) -> bytes:
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

    meta = FileMetaDataset()
    sop = generate_uid()
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = sop
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID, ds.SOPInstanceUID = SecondaryCaptureImageStorage, sop
    ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
    ds.PatientName = f"{p.last}^{p.first}"
    ds.PatientID = p.mrn
    ds.PatientBirthDate = "19310314"
    ds.PatientAge = "095Y"
    ds.PatientSex = "F"
    ds.PatientAddress = f"{p.street}, {p.city}"
    ds.PatientTelephoneNumbers = p.phone
    ds.StudyDate, ds.SeriesDate, ds.StudyTime = "20220517", "20220517", "093000"
    ds.AccessionNumber = f"ACC{s.digits(7)}"
    ds.InstitutionName = f"{p.city} Imaging Center"
    ds.ReferringPhysicianName = f"{doctor.last}^{doctor.first}"
    ds.OperatorsName = f"{doctor.first} {doctor.last}"
    ds.StudyDescription = f"Chest film for {p.name}"
    ds.StationName = "CT-ROOM-3"
    ds.DeviceSerialNumber = f"SN{s.digits(8)}"
    ds.Modality = "OT"
    block = ds.private_block(0x0009, "SYNTHVENDOR", create=True)
    block.add_new(0x10, "LO", f"Private note about {p.name}")
    img = Image.new("L", (768, 768), 20)
    d = ImageDraw.Draw(img)
    d.ellipse([180, 220, 590, 700], fill=90)
    d.text((24, 20), f"{p.last.upper()}, {p.first.upper()}", fill=255, font=font(34))
    d.text((24, 70), f"MRN {p.mrn}", fill=255, font=font(34))
    arr = np.array(img, dtype=np.uint8)
    ds.Rows, ds.Columns = arr.shape
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 8, 8, 7, 0
    ds.PixelData = arr.tobytes()
    out = io.BytesIO()
    ds.save_as(out, enforce_file_format=True)
    return out.getvalue()


def handwriting_like(text: str = "Pt seen today feeling better follow up soon", seed: int = 3) -> Image.Image:
    """Characters individually rotated and bounced - Tesseract reads it, unsure (like handwriting)."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (1500, 300), "white")
    x = 30
    for ch in text:
        tile = Image.new("L", (60, 80), 0)
        ImageDraw.Draw(tile).text((10, 10), ch, fill=255, font=font(44))
        tile = tile.rotate(rng.uniform(-35, 35))
        img.paste((0, 0, 0), (x, int(100 + rng.uniform(-25, 25))), tile)
        x += int(28 + rng.uniform(-6, 10))
    return img
