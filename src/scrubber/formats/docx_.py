"""Word (.docx) scrubbing at the run level, so formatting survives.

Detection runs on each paragraph's full text; replacements are written back
into the runs they came from (a name split across a bold and a plain run is
still found, and its token keeps the first run's formatting). Covered: body,
tables, text boxes, headers/footers, footnotes/endnotes, comments, tracked
insertions and deletions, field codes, hyperlink targets, document
properties, custom XML. Embedded images are blanked until Phase 3 adds
face/OCR redaction; embedded spreadsheets/documents are scrubbed recursively.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from pathlib import Path

from lxml import etree

from ..transform.scrub import apply_spans_to_segments
from .base import Context

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def q(tag: str, ns: str = W) -> str:
    return f"{{{ns}}}{tag}"


P, T, DEL_TEXT, INSTR, TAB, BR, CR, DEL = q("p"), q("t"), q("delText"), q("instrText"), q("tab"), q("br"), q("cr"), q("del")
SEPARATORS = {TAB: "\t", BR: "\n", CR: "\n", q("noBreakHyphen"): "-"}
AUTHOR_ATTRS = (q("author"), q("author", W15))
SKIP_PARTS = re.compile(r"^/word/(styles|stylesWithEffects|numbering|fontTable|webSettings|theme/.*)\.xml$")
APP_KEEP = {"Pages", "Words", "Characters", "CharactersWithSpaces", "Lines", "Paragraphs", "TotalTime",
            "DocSecurity", "ScaleCrop", "LinksUpToDate", "SharedDoc", "HyperlinksChanged", "AppVersion",
            "Application"}
SCRUBBER_NAME = "Data Frenzy Scrubber"


def _owner_p(el):
    anc = el.getparent()
    while anc is not None and anc.tag != P:
        anc = anc.getparent()
    return anc


def _in_deletion(el) -> bool:
    anc = el.getparent()
    while anc is not None and anc.tag != P:
        if anc.tag in (DEL, q("moveFrom")):
            return True
        anc = anc.getparent()
    return False


def _streams(p) -> tuple[list, list]:
    """(visible, deleted) streams of (element or None, text) for one paragraph."""
    visible, deleted = [], []
    for el in p.iter(T, DEL_TEXT, TAB, BR, CR, q("noBreakHyphen")):
        if _owner_p(el) is not p:
            continue  # belongs to a nested paragraph (text box)
        target = deleted if (el.tag == DEL_TEXT or _in_deletion(el)) else visible
        if el.tag in (T, DEL_TEXT):
            target.append((el, el.text or ""))
        else:
            target.append((None, SEPARATORS[el.tag]))
    return visible, deleted


def _set_text(el, text: str) -> None:
    el.text = text
    if text != text.strip():
        el.set(f"{{{XML_NS}}}space", "preserve")


def scrub_paragraphs(root, ctx: Context) -> list[str]:
    """Scrub every paragraph under root. Returns the new paragraph texts (for verification)."""
    streams = []
    for p in root.iter(P):
        for stream in _streams(p):
            if any(el is not None and txt.strip() for el, txt in stream):
                streams.append(stream)
    texts = ["".join(t for _, t in s) for s in streams]
    plans = ctx.scrubber.plan_many(texts, ctx.tally)
    out_texts = []
    for stream, text, spans in zip(streams, texts, plans):
        if spans:
            new = apply_spans_to_segments([t for _, t in stream], spans)
            for (el, old), txt in zip(stream, new):
                if el is not None and txt != old:
                    _set_text(el, txt)
            out_texts.append("".join(new))
        else:
            out_texts.append(text)
    # field codes (HYPERLINK "mailto:...", MERGEFIELD ...) and simple fields
    for el in root.iter(INSTR):
        if el.text and el.text.strip():
            _set_text(el, _scrub_field(el.text, ctx))
    for el in root.iter(q("fldSimple")):
        instr = el.get(q("instr"))
        if instr:
            el.set(q("instr"), _scrub_field(instr, ctx))
    return out_texts


def _scrub_field(code: str, ctx: Context) -> str:
    def link(m):
        target = m.group(1)
        if target.lower().startswith("mailto:"):
            ctx.tally.counts["EMAIL_ADDRESS"] += 1
            return f'"mailto:{ctx.pseudo.email_address(target[7:])}"'
        ctx.tally.counts["URL"] += 1
        return f'"https://scrubbed.invalid/{ctx.pseudo.token("URL", target)}"'
    code = re.sub(r'"((?:mailto:|https?://|ftp://|file:|\\\\)[^"]*)"', link, code)
    return ctx.scrubber.scrub(code, ctx.tally).text


def scrub_attributes(root, ctx: Context) -> None:
    for el in root.iter():
        for attr in AUTHOR_ATTRS:
            v = el.get(attr)
            if v:
                ctx.tally.counts["PERSON"] += 1
                el.set(attr, ctx.pseudo.token("PERSON", v))
        if el.get(q("initials")):
            el.set(q("initials"), "")
        d = el.get(q("date"))
        if d:
            y = ctx.gen.year_of(d)
            el.set(q("date"), f"{y}-01-01T00:00:00Z" if y else "2000-01-01T00:00:00Z")
            ctx.tally.counts["DATE"] += 1
        if el.tag == q("presenceInfo", W15):
            el.set(q("providerId", W15), "None")
            el.set(q("userId", W15), ctx.pseudo.opaque_id(el.get(q("userId", W15)) or ""))
        if el.tag == q("docVar"):
            el.set(q("val"), ctx.scrubber.scrub(el.get(q("val")) or "", ctx.tally).text)
        if el.tag.endswith("}docPr") or el.tag.endswith("}cNvPr"):
            for a in ("descr", "title"):
                if el.get(a):
                    el.set(a, ctx.scrubber.scrub(el.get(a), ctx.tally).text)


def scrub_leaf_text(root, ctx: Context, keep: set[str] = frozenset()) -> None:
    leaves = [el for el in root.iter() if isinstance(el.tag, str) and len(el) == 0 and el.text and el.text.strip()
              and etree.QName(el).localname not in keep]
    for el, res in zip(leaves, ctx.scrubber.scrub_many([el.text for el in leaves], ctx.tally)):
        if res.findings:
            el.text = res.text


def _external_target(target: str, ctx: Context) -> str:
    if target.lower().startswith("mailto:"):
        ctx.tally.counts["EMAIL_ADDRESS"] += 1
        return "mailto:" + ctx.pseudo.email_address(target[7:].split("?")[0])
    if re.match(r"(?i)^(https?|ftp)://", target):
        ctx.tally.counts["URL"] += 1
        return f"https://scrubbed.invalid/{ctx.pseudo.token('URL', target)}"
    if target.startswith("#"):
        return target
    # file paths (attached templates, linked files) carry user names and folders
    parts = re.split(r"([\\/])", target)
    return "".join(p if p in ("\\", "/") else ctx.scrubber.scrub_name(p, ctx.tally) for p in parts)


def _blank_image(blob: bytes) -> bytes:
    from PIL import Image
    try:
        with Image.open(io.BytesIO(blob)) as im:
            fmt, size, mode = im.format or "PNG", im.size, ("RGB" if im.mode not in ("L", "1") else "L")
    except Exception:
        fmt, size, mode = "PNG", (1, 1), "RGB"
    out = io.BytesIO()
    Image.new(mode, size, (200, 200, 200) if mode == "RGB" else 200).save(out, format=fmt)
    return out.getvalue()


def _scrub_picture(blob: bytes, ctx: Context, where: str, depth: int) -> bytes:
    """OCR-redact and face-blur a picture; vector formats (EMF/WMF) can't be read, so they are blanked."""
    from .image import scrub_image_bytes
    try:
        return scrub_image_bytes(blob, ctx, where, depth)
    except Exception:
        ctx.quarantine(where, "picture in a format that can't be checked (e.g. EMF/WMF) - replaced with a blank")
        return _blank_image(blob)


def _core_properties(doc, ctx: Context) -> None:
    cp = doc.core_properties
    for attr in ("author", "last_modified_by"):
        if getattr(cp, attr):
            ctx.tally.counts["PERSON"] += 1
            setattr(cp, attr, SCRUBBER_NAME)
    for attr in ("title", "subject", "keywords", "comments", "category", "content_status", "identifier", "version"):
        v = getattr(cp, attr, None)
        if v:
            setattr(cp, attr, ctx.scrubber.scrub(str(v), ctx.tally).text)
    for attr in ("created", "modified", "last_printed"):
        v = getattr(cp, attr)
        if v:
            setattr(cp, attr, dt.datetime(v.year, 1, 1))
            ctx.tally.years.add(v.year)


def scrub_document(doc, ctx: Context, item: str, depth: int = 0) -> list[str]:
    """Scrub a python-docx Document in place. Returns all scrubbed paragraph text."""
    from docx.opc.part import XmlPart

    from .dispatch import scrub_bytes

    texts: list[str] = []
    package = doc.part.package
    for part in package.iter_parts():
        name = str(part.partname)
        ctype = part.content_type or ""
        for rel in part.rels.values():
            if rel.is_external:
                rel._target = _external_target(rel.target_ref, ctx)
        if name == "/docProps/core.xml" or SKIP_PARTS.match(name):
            continue  # core properties are handled below; styles/themes hold no document text
        is_xml = isinstance(part, XmlPart) or ctype.endswith("xml")
        if is_xml and (name.startswith(("/word/", "/customXml/")) or name in ("/docProps/app.xml", "/docProps/custom.xml")):
            if isinstance(part, XmlPart):
                root = part._element
            else:
                try:
                    root = etree.fromstring(part.blob)
                except etree.XMLSyntaxError:
                    continue
            if name.startswith("/word/") and any(True for _ in root.iter(P)):
                texts += scrub_paragraphs(root, ctx)  # document, headers, footers, footnotes, comments ...
                scrub_attributes(root, ctx)
            else:
                scrub_attributes(root, ctx)
                scrub_leaf_text(root, ctx, keep=APP_KEEP if name.endswith("app.xml") else set())
            if not isinstance(part, XmlPart):
                part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        elif ctype.startswith("image/") or name.startswith("/word/media/"):
            part._blob = _scrub_picture(part.blob, ctx, f"{item} image {Path(name).name}", depth)
        elif name.startswith("/word/embeddings/"):
            out = None
            try:
                out = scrub_bytes(Path(name).name, part.blob, ctx, f"{item} embedded {Path(name).name}", depth=depth)
            except Exception:
                out = None
            if out is None:
                ctx.quarantine(f"{item} embedded object {Path(name).name}", "embedded object emptied (format not supported)")
                out = b""
            part._blob = out
    _core_properties(doc, ctx)
    return texts


def all_text(doc) -> list[str]:
    from docx.opc.part import XmlPart

    out = []
    for part in doc.part.package.iter_parts():
        name = str(part.partname)
        if not name.startswith("/word/") or SKIP_PARTS.match(name):
            continue
        if isinstance(part, XmlPart):
            root = part._element
        elif (part.content_type or "").endswith("xml"):
            root = etree.fromstring(part.blob)
        else:
            continue
        if True:
            for p in root.iter(P):
                for stream in _streams(p):
                    text = "".join(t for _, t in stream)
                    if text.strip():
                        out.append(text)
            out += [el.text for el in root.iter(INSTR) if el.text and el.text.strip()]
    cp = doc.core_properties
    out += [str(v) for v in (cp.title, cp.subject, cp.keywords, cp.comments, cp.category) if v]
    return out


def verify_docx(data: bytes, body_paragraphs: int, ctx: Context, item: str) -> None:
    import docx
    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as exc:
        ctx.issue(f"{item}: scrubbed document does not open ({type(exc).__name__})")
        return
    if len(doc.paragraphs) != body_paragraphs:
        ctx.issue(f"{item}: paragraph count changed during scrubbing")
    if doc.core_properties.author not in ("", None, SCRUBBER_NAME):
        ctx.issue(f"{item}: author property survived scrubbing")
    texts = all_text(doc)
    for i in range(0, len(texts), 500):
        ctx.residual("\n\n".join(texts[i:i + 500]), item)


def scrub_docx_bytes(data: bytes, ctx: Context, item: str, depth: int = 0) -> bytes:
    import docx
    doc = docx.Document(io.BytesIO(data))
    n = len(doc.paragraphs)
    scrub_document(doc, ctx, item, depth)
    out = io.BytesIO()
    doc.save(out)
    result = out.getvalue()
    verify_docx(result, n, ctx, item)
    ctx.tally.records += n
    return result


def process_docx(src: Path, dst: Path, ctx: Context) -> None:
    out = scrub_docx_bytes(src.read_bytes(), ctx, ctx.out_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
