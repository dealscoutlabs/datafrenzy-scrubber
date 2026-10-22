import io
import re

import numpy as np
import pytest
from PIL import Image

from fixtures import synth_images as si
from scrubber.detect.vision import FaceDetector
from scrubber.formats.image import has_metadata, scrub_image_bytes

import pytesseract


def _ocr_all(img: Image.Image) -> str:
    return pytesseract.image_to_string(img.convert("RGB"))


def _secrets(p):
    return [p.last, p.mrn, p.email, p.ssn, re.sub(r"\D", "", p.phone)[-4:]]


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "TIFF", "WEBP", "BMP", "GIF"])
def test_text_identifiers_painted_over(ctx, synth, fmt):
    p = synth.person()
    src = si.text_image(si.note_lines(synth, p))
    data = si.save(src if fmt != "GIF" else src.convert("P"), fmt)
    out = Image.open(io.BytesIO(scrub_image_bytes(data, ctx, f"note.{fmt.lower()}")))
    assert out.format == fmt and out.size == src.size
    text = _ocr_all(out)
    for s in _secrets(p):
        assert s not in text, (fmt, s, text)
    assert "Patient" in text  # labels stay readable
    assert ctx.tally.issues == []


def test_exif_gps_and_camera_metadata_stripped(ctx, synth):
    data = si.jpeg_with_gps(si.text_image(["Visit photo"]))
    assert {"GPS", "Make", "Artist"} <= set(has_metadata(Image.open(io.BytesIO(data))))
    out = Image.open(io.BytesIO(scrub_image_bytes(data, ctx, "photo.jpg")))
    assert has_metadata(out) == []
    assert b"SynthCam" not in scrub_image_bytes(data, ctx, "photo.jpg")


def test_exif_rotation_baked_in(ctx):
    img = si.text_image(["Rotated"], width=600)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 on display
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    out = Image.open(io.BytesIO(scrub_image_bytes(buf.getvalue(), ctx, "rot.jpg")))
    assert out.size == (img.height, img.width) and 0x0112 not in out.getexif()


def test_faces_blurred_beyond_detection(ctx, synth):
    p = synth.person()
    data = si.jpeg_with_gps(si.id_badge(p))
    fd = FaceDetector(0.6)
    assert fd.detect(np.array(Image.open(io.BytesIO(data)).convert("RGB")))  # the drawn face is a face
    out = Image.open(io.BytesIO(scrub_image_bytes(data, ctx, "badge.jpg"))).convert("RGB")
    assert fd.detect(np.array(out), 0.2) == []
    assert ctx.tally.counts["FACE_IMAGE"] == 1
    text = _ocr_all(out)
    assert p.last not in text and p.mrn not in text


def test_multipage_tiff_and_heic(ctx, synth):
    p = synth.person()
    pages = [si.text_image(si.note_lines(synth, p)), si.id_badge(p)]
    out = Image.open(io.BytesIO(scrub_image_bytes(si.tiff_pages(pages), ctx, "chart.tif")))
    assert out.n_frames == 2
    heic = Image.open(io.BytesIO(scrub_image_bytes(si.heic(si.id_badge(p)), ctx, "IMG_0001.heic")))
    assert heic.format == "HEIF"
    assert FaceDetector(0.6).detect(np.array(heic.convert("RGB")), 0.2) == []


def test_low_confidence_ocr_goes_to_review(ctx):
    scrub_image_bytes(si.save(si.handwriting_like(), "PNG"), ctx, "note.png")
    assert any("OCR confidence" in i or "handwriting" in i for i in ctx.tally.issues)


def test_tiny_images_still_lose_metadata(ctx):
    data = si.jpeg_with_gps(Image.new("RGB", (16, 16), (10, 20, 30)))
    out = Image.open(io.BytesIO(scrub_image_bytes(data, ctx, "icon.jpg")))
    assert has_metadata(out) == [] and out.size == (16, 16)
