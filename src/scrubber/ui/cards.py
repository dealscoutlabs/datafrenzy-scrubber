"""Card review: everything the double-check flagged in a batch, one card per distinct item.

The same leftover text ("ACME PAYROLL INC" on 19 tax forms) is one card, decided once:
Good (it's fine, everywhere) or PII (black it out, everywhere). Problems with a whole file
(a hard-to-read scan) are one card per file: Good includes it, PII leaves it out.

Decisions are applied when the reviewer finishes: Good texts are remembered (config
known_fine) so they aren't flagged again; PII texts join the deny list and the batch is
re-scrubbed; files whose every card is Good are released. Local only, like the review queue.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

from ..config import add_deny_terms, add_known_fine
from ..formats.base import fine_key
from ..identifiers import TOKEN_PREFIX

# what the card says the item might be
WHAT = {"PERSON": "a name", "DATE": "a date", "DATE_TIME": "a date", "LOCATION": "a place",
        "STREET_ADDRESS": "an address", "ZIP_CODE": "a ZIP code", "PHONE_NUMBER": "a phone number",
        "FAX_NUMBER": "a fax number", "EMAIL_ADDRESS": "an email address", "US_SSN": "a Social Security number",
        "URL": "a web address", "IP_ADDRESS": "an IP address", "ORGANIZATION": "an organization name",
        "MEDICAL_RECORD_NUMBER": "a record number", "HEALTH_PLAN_ID": "a health plan number",
        "ACCOUNT_NUMBER": "an account number", "LICENSE_NUMBER": "a licence number", "VEHICLE_ID": "a vehicle number",
        "DEVICE_ID": "a device number", "CONTEXT": "a detail that could point to one person"}
_ETYPE = re.compile(r"residual (\w+)")
_PAGE = re.compile(r" page (\d+)")
_MARKED = re.compile(r"⟦(.*?)⟧", re.S)
CROPPABLE = {"pdf", "image"}


def _flag_parts(d: dict) -> tuple[str | None, str | None, int | None]:
    """(value, etype, page) of a flag; older flags only carry them inside the message and snippet."""
    value, etype, page = d.get("value"), d.get("etype"), d.get("page")
    msg = d.get("message") or ""
    if value is None and d.get("snippet"):
        m = _MARKED.search(d["snippet"])
        value = m.group(1) if m else None
    if etype is None:
        m = _ETYPE.search(msg)
        etype = m.group(1) if m else ("CONTEXT" if d.get("kind") == "context-risk" else None)
    if page is None:
        m = _PAGE.search(msg)
        page = int(m.group(1)) if m else None
    return value, etype, page


def card_id(d: dict, rel: str) -> str:
    value, _, _ = _flag_parts(d)
    if d.get("kind") in ("residual", "context-risk") and value and fine_key(value):
        return "v" + hashlib.sha256(fine_key(value).encode()).hexdigest()[:16]
    return "f" + hashlib.sha256(f"{rel}\0{d.get('message')}".encode()).hexdigest()[:16]


def _plain(message: str) -> str:
    text = message.split(": ", 1)[-1]
    if "OCR confidence" in text:
        return "Hard-to-read scan, so some text may have been missed. Check the whole page."
    return text[0].upper() + text[1:] if text else "The double-check wasn't sure about this file."


def deck(store) -> list[dict]:
    """Cards still waiting for a decision, the most widespread first."""
    decided = store.cards()
    files = {f["rel"]: f for f in store.files()}
    cards: dict[str, dict] = {}
    for rv in store.reviews("pending"):
        f = files.get(rv["rel"])
        if f is None:
            continue
        for d in json.loads(rv["details"] or "[]"):
            cid = card_id(d, rv["rel"])
            if cid in decided:
                continue
            value, etype, page = _flag_parts(d)
            c = cards.get(cid)
            if c is None:
                item = cid.startswith("v")
                c = cards[cid] = {"id": cid, "kind": "item" if item else "file", "value": value if item else None,
                                  "what": WHAT.get(etype or "", "an ID number") if item else None,
                                  "message": None if item else _plain(d.get("message") or ""),
                                  "snippet": d.get("snippet"), "files": [], "crop": False}
            if rv["rel"] not in c["files"]:
                c["files"].append(rv["rel"])
            if not c["crop"] and f["kind"] in CROPPABLE and (page or f["kind"] == "image"):
                c["crop"] = True
    out = sorted(cards.values(), key=lambda c: (-len(c["files"]), c["kind"] != "item", (c["value"] or "").lower()))
    for c in out:
        c["count"] = len(c["files"])
        c["files"] = c["files"][:3]
    return out


def find_spot(store, cid: str):
    """(file row, page, value) of one place this card's item appears, preferring a croppable one."""
    files = {f["rel"]: f for f in store.files()}
    fallback = None
    for rv in store.reviews("pending"):
        f = files.get(rv["rel"])
        for d in json.loads(rv["details"] or "[]"):
            if f is None or card_id(d, rv["rel"]) != cid:
                continue
            value, _, page = _flag_parts(d)
            if f["kind"] in CROPPABLE:
                return f, page or 1, value
            fallback = fallback or (f, page, value)
    return fallback


def crop_png(path: Path, kind: str, page: int, value: str | None) -> bytes | None:
    """A strip of the scrubbed page around the flagged text, the text boxed in yellow.
    For whole-file cards (no value) the page itself, scaled down."""
    from PIL import Image, ImageDraw, ImageOps
    boxes, img = [], None
    if kind == "pdf":
        import pymupdf
        with pymupdf.open(path) as doc:
            pg = doc[min(max(page, 1), doc.page_count) - 1]
            zoom = 2.0
            rects = pg.search_for(value) if value else []
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            boxes = [(r.x0 * zoom, r.y0 * zoom, r.x1 * zoom, r.y1 * zoom) for r in rects[:1]]
    else:
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if value and not boxes:
        boxes = _ocr_find(img, value)       # scanned page or photo: find the words by OCR
    if not boxes:
        if value:
            return None
        img.thumbnail((900, 900))
        return _png(img)
    x0, y0, x1, y1 = boxes[0]
    pad_x, pad_y = max(260, (x1 - x0)), max(70, (y1 - y0) * 2.5)
    box = (max(0, int(x0 - pad_x)), max(0, int(y0 - pad_y)), min(img.width, int(x1 + pad_x)),
           min(img.height, int(y1 + pad_y)))
    img = img.crop(box)
    over = Image.new("RGBA", img.size)
    draw = ImageDraw.Draw(over)
    r = (x0 - box[0] - 4, y0 - box[1] - 3, x1 - box[0] + 4, y1 - box[1] + 3)
    draw.rectangle(r, fill=(255, 214, 0, 70), outline=(230, 160, 0, 255), width=3)
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    if img.width > 1000:
        img = img.resize((1000, int(img.height * 1000 / img.width)))
    return _png(img)


def _ocr_find(img, value: str) -> list[tuple]:
    import pytesseract
    want = fine_key(value).split()
    if not want:
        return []
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words = [(fine_key(t), (data["left"][i], data["top"][i], data["left"][i] + data["width"][i],
                            data["top"][i] + data["height"][i])) for i, t in enumerate(data["text"]) if t.strip()]
    for i in range(len(words)):
        run = words[i:i + len(want)]
        if [w for w, _ in run] == want:
            bs = [b for _, b in run]
            return [(min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))]
    return []


def _png(img) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def finish(runner, config_path: str, reviewer: str) -> dict:
    """Apply the decisions: remember Good texts, deny-list PII texts, then release every held
    file whose cards are all Good, leave out files a reviewer left out, and ask for a re-scrub
    when anything was marked PII. Files with cards still undecided stay held."""
    store = runner.store
    decided = store.cards()
    fresh = [c for c in decided.values() if not c["applied"]]
    good = [c["value"] for c in fresh if c["decision"] == "good" and c["card"].startswith("v") and c["value"]]
    pii = [c for c in fresh if c["decision"] == "pii" and c["card"].startswith("v") and c["value"]]
    if good:
        add_known_fine(config_path, good, reviewer)
    for c in pii:
        etype = c["etype"] if c["etype"] in TOKEN_PREFIX else "OTHER_ID"
        add_deny_terms(config_path, etype, [c["value"]])
    released = left_out = rescrub = waiting = 0
    for rv in store.reviews("pending"):
        ids = [card_id(d, rv["rel"]) for d in json.loads(rv["details"] or "[]")]
        if not ids or any(i not in decided for i in ids):
            waiting += 1
            continue
        calls = {decided[i]["decision"] for i in ids}
        try:
            if any(decided[i]["decision"] == "pii" and i.startswith("f") for i in ids):
                runner.reject(rv["rel"], reviewer, "card review: left out")
                left_out += 1
            elif calls == {"good"}:
                runner.approve(rv["rel"], reviewer, "card review: every flagged item marked Good")
                released += 1
            else:
                rescrub += 1
        except ValueError:
            continue
    store.mark_cards_applied()
    runner.write_summary()
    return {"released": released, "left_out": left_out, "rescrub": rescrub, "waiting": waiting,
            "rerun": bool(pii)}
