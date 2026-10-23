"""Face-blurring evaluation on a folder of face images (e.g. the synthetic
SFHQ dataset). Measures, per image and per scale:

- detected:  the face detector found at least one face before blurring
- leaked:    after blurring, the detector still finds a face at a low (0.2)
             threshold - i.e. the blur did not remove the face

Faces are also shrunk onto a larger canvas (25% and 10% of the width) to
mimic people in the background of a photo. Results go to a CSV and a summary.
Runs offline like everything else.
"""

from __future__ import annotations

import csv
import random
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .detect.vision import FaceDetector, blur_regions

SCALES = {"full": 1.0, "quarter": 0.25, "tenth": 0.10}
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def _place(img: Image.Image, share: float, rng: random.Random) -> Image.Image:
    if share >= 1.0:
        return img
    canvas = Image.new("RGB", (1600, 1200), tuple(rng.randint(60, 200) for _ in range(3)))
    w = int(canvas.width * share)
    small = img.resize((w, int(img.height * w / img.width)))
    x = rng.randint(0, canvas.width - small.width)
    y = rng.randint(0, canvas.height - small.height)
    canvas.paste(small, (x, y))
    return canvas


def evaluate(folder: Path, out_csv: Path, limit: int = 1000, threshold: float = 0.6, seed: int = 7) -> dict:
    rng = random.Random(seed)
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in EXTS)
    rng.shuffle(files)
    files = files[:limit]
    det = FaceDetector(threshold)
    rows, t0 = [], time.time()
    for p in files:
        base = Image.open(p).convert("RGB")
        for scale, share in SCALES.items():
            img = np.array(_place(base, share, rng))
            faces = det.detect(img)
            leaked = det.detect(blur_regions(img, faces), 0.2) if faces else []
            rows.append({"file": p.name, "scale": scale, "detected": int(bool(faces)), "faces": len(faces),
                         "leaked": int(bool(leaked))})
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "scale", "detected", "faces", "leaked"])
        w.writeheader()
        w.writerows(rows)
    summary = {"images": len(files), "seconds": round(time.time() - t0, 1), "threshold": threshold}
    for scale in SCALES:
        sub = [r for r in rows if r["scale"] == scale]
        found = [r for r in sub if r["detected"]]
        summary[scale] = {"detection_rate": round(len(found) / len(sub), 4) if sub else None,
                          "leak_rate_after_blur": round(sum(r["leaked"] for r in found) / len(found), 4) if found else None}
    return summary
