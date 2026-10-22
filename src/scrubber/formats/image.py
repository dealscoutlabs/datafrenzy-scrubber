"""Images (PNG, JPEG, TIFF, HEIC, GIF, BMP, WebP), also used for pictures inside
emails, Word files and PDFs, and for scanned PDF pages.

1. Bake in the EXIF rotation, then drop all metadata (EXIF, GPS, camera, XMP, comments).
2. OCR with Tesseract; paint over every identifier's box (presidio ImageRedactorEngine).
3. Detect faces (OpenCV YuNet) and pixelate + blur them.
4. Low OCR confidence or handwriting-like text -> human review.
5. Re-open the saved file: it must load, carry no metadata, and show no
   identifiers (re-OCR) or faces (re-detection).
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, ImageSequence

from .base import Context

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pillow_heif = None

SAVE_FORMATS = {"JPEG", "PNG", "TIFF", "GIF", "BMP", "WEBP", "HEIF", "MPO"}
MIN_SIDE_FOR_OCR = 24


class Vision:
    """Per-process OCR/face engines (created lazily; not shared across forks)."""

    def __init__(self, ctx: Context):
        from presidio_image_redactor import ImageRedactorEngine

        from ..detect.vision import FaceDetector, ScrubberImageAnalyzer
        cfg = ctx.cfg["images"]
        self.analyzer = ScrubberImageAnalyzer(ctx.scrubber.detector)
        self.redactor = ImageRedactorEngine(image_analyzer_engine=self.analyzer)
        self.faces = FaceDetector(float(cfg["face_score_threshold"]))


def vision(ctx: Context) -> Vision:
    v = getattr(ctx.scrubber, "_vision", None)
    if v is None:
        v = Vision(ctx)
        ctx.scrubber._vision = v
    return v


def _ocr_scale(img: Image.Image, max_side: int) -> Image.Image:
    if max(img.size) <= max_side:
        return img
    ratio = max_side / max(img.size)
    return img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)


def scrub_frame(img: Image.Image, ctx: Context, item: str) -> Image.Image:
    """Redact text identifiers and faces in one RGB(A)/L image. Returns a new image."""
    cfg = ctx.cfg["images"]
    v = vision(ctx)
    alpha = img.getchannel("A") if img.mode in ("RGBA", "LA") else None
    rgb = img.convert("RGB")
    if min(rgb.size) >= MIN_SIDE_FOR_OCR:
        small = _ocr_scale(rgb, int(cfg["max_ocr_side"]))
        v.analyzer.adapter.findings = []
        redacted_small = v.redactor.redact(small, fill=(0, 0, 0))
        if small is not rgb:
            # paint the same boxes at full resolution
            rgb = _paint_scaled(rgb, small, redacted_small)
        else:
            rgb = redacted_small
        stats = v.analyzer.last
        found = v.analyzer.adapter.findings
        ctx.scrubber._count(found, ctx.tally)
        if stats.words >= 3 and stats.mean_conf < float(cfg["min_ocr_confidence"]):
            ctx.issue(f"{item}: low OCR confidence ({stats.mean_conf:.0f}) - text may have been missed")
        if stats.words >= 5 and stats.low_share > float(cfg["handwriting_share"]):
            ctx.issue(f"{item}: possible handwriting - check by eye")
    arr = np.array(rgb)
    faces = v.faces.detect(arr)
    if faces:
        from ..detect.vision import blur_regions
        arr = blur_regions(arr, faces)
        ctx.tally.counts["FACE_IMAGE"] += len(faces)
    out = Image.fromarray(arr)
    if alpha is not None:
        out.putalpha(alpha)
    if img.mode == "L":
        out = out.convert("L")
    return out


def _paint_scaled(full: Image.Image, small: Image.Image, redacted_small: Image.Image) -> Image.Image:
    """Copy redaction boxes found on a downscaled copy onto the full-size image."""
    diff = np.any(np.array(small) != np.array(redacted_small), axis=2)
    if not diff.any():
        return full
    mask = Image.fromarray((diff * 255).astype(np.uint8)).resize(full.size, Image.NEAREST)
    black = Image.new("RGB", full.size, (0, 0, 0))
    return Image.composite(black, full, mask)


def _save(frames: list[Image.Image], fmt: str, src: Image.Image) -> bytes:
    out = io.BytesIO()
    info = src.info
    fmt = "JPEG" if fmt == "MPO" else fmt
    first = frames[0]
    kwargs: dict = {}
    if "icc_profile" in info:
        kwargs["icc_profile"] = info["icc_profile"]  # colour profile only; not identifying
    if fmt == "JPEG":
        first = first.convert("RGB") if first.mode not in ("RGB", "L", "CMYK") else first
        kwargs.update(quality=95, optimize=True)
        first.save(out, "JPEG", **kwargs)
    elif fmt == "PNG":
        first.save(out, "PNG", **kwargs)
    elif fmt == "TIFF":
        comp = info.get("compression")
        if comp not in ("tiff_lzw", "tiff_adobe_deflate", "raw", "packbits", "jpeg", "group4"):
            comp = "tiff_lzw"
        if comp == "group4" and first.mode != "1":
            comp = "tiff_lzw"
        first.save(out, "TIFF", save_all=len(frames) > 1, append_images=frames[1:], compression=comp, **kwargs)
    elif fmt == "GIF":
        first.save(out, "GIF", save_all=len(frames) > 1, append_images=frames[1:],
                   duration=info.get("duration", 100), loop=info.get("loop", 0))
    elif fmt == "WEBP":
        first.save(out, "WEBP", quality=95, save_all=len(frames) > 1, append_images=frames[1:])
    elif fmt == "HEIF":
        first.save(out, "HEIF", quality=95)
    else:
        first.save(out, fmt)
    return out.getvalue()


# EXIF/TIFF tags that describe a person, a device, a place or a time. Structural
# TIFF tags (width, compression, strip offsets ...) are not metadata about anyone.
IDENTIFYING_TAGS = {0x010E: "ImageDescription", 0x010F: "Make", 0x0110: "Model", 0x0131: "Software",
                    0x0132: "DateTime", 0x013B: "Artist", 0x013C: "HostComputer", 0x8298: "Copyright",
                    0x8769: "ExifIFD", 0x8825: "GPS", 0x9C9B: "XPTitle", 0x9C9C: "XPComment",
                    0x9C9D: "XPAuthor", 0x9C9E: "XPKeywords", 0x9C9F: "XPSubject", 0xA420: "ImageUniqueID",
                    0xA430: "CameraOwnerName", 0xA431: "BodySerialNumber", 0x02BC: "XMP", 0x83BB: "IPTC",
                    0x8649: "Photoshop"}


def has_metadata(img: Image.Image) -> list[str]:
    found = []
    try:
        exif = img.getexif()
        found += [name for tag, name in IDENTIFYING_TAGS.items() if tag in exif]
    except Exception:
        pass
    for key in ("xmp", "XML:com.adobe.xmp", "comment", "exif", "photoshop", "iptc"):
        if img.info.get(key):
            found.append(key)
    return found


def verify_image(data: bytes, expected_frames: int, ctx: Context, item: str) -> None:
    v = vision(ctx)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        ctx.issue(f"{item}: scrubbed image does not open ({type(exc).__name__})")
        return
    meta = has_metadata(img)
    if meta:
        ctx.issue(f"{item}: metadata survived ({', '.join(meta)})")
    frames = getattr(img, "n_frames", 1)
    if frames != expected_frames:
        ctx.issue(f"{item}: frame/page count changed during scrubbing")
    from ..detect.vision import ocr_text
    for frame in ImageSequence.Iterator(img):
        rgb = frame.convert("RGB")
        if min(rgb.size) >= MIN_SIDE_FOR_OCR:
            ctx.residual(ocr_text(_ocr_scale(rgb, int(ctx.cfg["images"]["max_ocr_side"]))), item)
        strict = float(ctx.cfg["images"]["face_score_threshold"])
        if v.faces.detect(np.array(rgb), strict):
            ctx.issue(f"{item}: a face is still detectable after blurring")


def scrub_image_bytes(data: bytes, ctx: Context, item: str, depth: int = 0) -> bytes:
    src = Image.open(io.BytesIO(data))
    fmt = (src.format or "PNG").upper()
    if fmt not in SAVE_FORMATS:
        fmt = "PNG"
    frames = []
    n = getattr(src, "n_frames", 1) if fmt != "MPO" else 1  # MPO: keep the main picture only
    for i, frame in enumerate(ImageSequence.Iterator(src)):
        if i >= n:
            break
        frame = ImageOps.exif_transpose(frame.copy())
        frames.append(scrub_frame(frame, ctx, item if n == 1 else f"{item} frame {i + 1}"))
    out = _save(frames, fmt, src)
    verify_image(out, len(frames), ctx, item)
    ctx.tally.records += 1
    return out


def scrub_pil(img: Image.Image, ctx: Context, item: str) -> Image.Image:
    """For callers that already have a PIL image (scanned PDF pages)."""
    return scrub_frame(img, ctx, item)


def process_image(src: Path, dst: Path, ctx: Context) -> None:
    out = scrub_image_bytes(src.read_bytes(), ctx, ctx.out_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
