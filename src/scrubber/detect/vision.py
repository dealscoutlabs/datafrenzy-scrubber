"""OCR and face detection, fully local.

- OCR: Tesseract through presidio-image-redactor's ImageAnalyzerEngine, with
  our Detector (all 18 identifier types, thresholds, allowlists) standing in
  for Presidio's default text analyzer.
- Faces: OpenCV YuNet (bundled ONNX model, data/models/).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import numpy as np
from presidio_analyzer import RecognizerResult
from presidio_image_redactor import ImageAnalyzerEngine, TesseractOCR

from ..config import DATA_DIR
from .engine import ENTITY_MAP, Detector

YUNET = DATA_DIR / "models" / "face_detection_yunet_2023mar.onnx"


class DetectorAdapter:
    """Looks like presidio_analyzer.AnalyzerEngine to the image redactor, runs our Detector."""

    def __init__(self, detector: Detector):
        self.detector = detector
        self.findings = []

    def analyze(self, text: str, language: str = "en", ad_hoc_recognizers=None, **_kwargs):
        found = self.detector.detect(text)
        results = [RecognizerResult(f.etype, f.start, f.end, f.score) for f in found]
        self.findings += found
        # DICOM: the redactor builds a deny list from the file's own tags (patient name, ID ...)
        for rec in ad_hoc_recognizers or []:
            for r in rec.analyze(text, rec.supported_entities):
                results.append(RecognizerResult(ENTITY_MAP.get(r.entity_type) or r.entity_type, r.start, r.end,
                                                max(r.score, 0.9)))
        return results


@dataclass
class OcrStats:
    words: int = 0
    mean_conf: float = 100.0
    low_share: float = 0.0          # share of words Tesseract was very unsure about
    text: str = ""
    confidences: list = field(default_factory=list)


class ScrubberImageAnalyzer(ImageAnalyzerEngine):
    """ImageAnalyzerEngine that also keeps the OCR result, for confidence gating."""

    def __init__(self, detector: Detector):
        self.adapter = DetectorAdapter(detector)
        super().__init__(analyzer_engine=self.adapter, ocr=TesseractOCR())
        self.last: OcrStats = OcrStats()

    def analyze(self, image, ocr_kwargs=None, **text_analyzer_kwargs):
        perform_ocr_kwargs, ocr_threshold = self._parse_ocr_kwargs(ocr_kwargs)
        image, meta = self.image_preprocessor.preprocess_image(image)
        ocr_result = self.remove_space_boxes(self.ocr.perform_ocr(image, **perform_ocr_kwargs))
        if meta and "scale_factor" in meta:
            ocr_result = self._scale_bbox_results(ocr_result, meta["scale_factor"])
        text = self.ocr.get_text_from_ocr_dict(ocr_result)
        confs = [float(c) for c, t in zip(ocr_result.get("conf", []), ocr_result.get("text", []))
                 if str(t).strip() and float(c) >= 0]
        self.last = OcrStats(
            words=len(confs), mean_conf=statistics.fmean(confs) if confs else 100.0,
            low_share=(sum(1 for c in confs if c < 30) / len(confs)) if confs else 0.0, text=text, confidences=confs)
        text_analyzer_kwargs.setdefault("language", "en")
        results = self.analyzer_engine.analyze(text=text, **text_analyzer_kwargs)
        return self.map_analyzer_results_to_bounding_boxes(results, ocr_result, text, [])


def ocr_text(image, min_conf: float = 60) -> str:
    """OCR text keeping only words Tesseract is reasonably sure of. Used for
    verification: the edges of painted-over boxes read as noise ("ii =", "Ena")."""
    import pytesseract
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    lines: dict = {}
    for text, conf, block, par, line in zip(data["text"], data["conf"], data["block_num"], data["par_num"],
                                            data["line_num"]):
        if str(text).strip() and float(conf) >= min_conf:
            lines.setdefault((block, par, line), []).append(str(text))
    return "\n".join(" ".join(words) for words in lines.values())


class FaceDetector:
    def __init__(self, score_threshold: float = 0.6, max_side: int = 1280):
        import cv2
        self.cv2 = cv2
        self.score_threshold = score_threshold
        self.max_side = max_side
        self._det = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), score_threshold, 0.3, 5000)

    def detect(self, rgb: np.ndarray, score_threshold: float | None = None) -> list[tuple[int, int, int, int, float]]:
        h, w = rgb.shape[:2]
        if min(h, w) < 32:
            return []
        scale = min(1.0, self.max_side / max(h, w))
        img = rgb if scale == 1.0 else self.cv2.resize(rgb, (int(w * scale), int(h * scale)))
        bgr = img[:, :, ::-1].copy() if img.ndim == 3 else self.cv2.cvtColor(img, self.cv2.COLOR_GRAY2BGR)
        self._det.setInputSize((bgr.shape[1], bgr.shape[0]))
        self._det.setScoreThreshold(score_threshold if score_threshold is not None else self.score_threshold)
        _, faces = self._det.detect(bgr)
        out = []
        for f in faces if faces is not None else []:
            x, y, fw, fh = (f[:4] / scale).tolist()
            out.append((int(x), int(y), int(fw), int(fh), float(f[-1])))
        return out


def blur_regions(rgb: np.ndarray, boxes, pad: float = 0.4, blocks: int = 4) -> np.ndarray:
    """Pixelate each box (expanded by pad) to a coarse blocks x blocks grid, then soften.
    Tested: the face detector finds nothing afterwards even at a 0.2 threshold."""
    import cv2
    out = rgb.copy()
    h, w = out.shape[:2]
    for x, y, bw, bh, *_ in boxes:
        x0, y0 = max(0, int(x - bw * pad)), max(0, int(y - bh * pad))
        x1, y1 = min(w, int(x + bw * (1 + pad))), min(h, int(y + bh * (1 + pad)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        roi = out[y0:y1, x0:x1]
        small = cv2.resize(roi, (blocks, blocks), interpolation=cv2.INTER_AREA)
        roi = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        k = max(3, (min(x1 - x0, y1 - y0) // 10) | 1)
        out[y0:y1, x0:x1] = cv2.GaussianBlur(roi, (k, k), 0)
    return out
