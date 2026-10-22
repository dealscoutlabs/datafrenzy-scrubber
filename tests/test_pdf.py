import re

import pymupdf

from conftest import assert_no_secrets
from fixtures.synth_docs import pdf_bytes, scanned_pdf_bytes
from scrubber.formats.pdf import scrub_pdf_bytes



def _scrub(synth, harvested):
    people = [synth.person() for _ in range(3)]
    src = pdf_bytes(synth, people, csv_text=synth.csv_text(people[:2]))
    ctx = harvested(people)
    out = scrub_pdf_bytes(src, ctx, "summary.pdf")
    return people, src, out, ctx


def _everything(doc) -> str:
    parts = [p.get_text() for p in doc]
    for p in doc:
        parts += [str(a.info) for a in p.annots()]
        parts += [str(w.field_value) for w in p.widgets()]
        parts += [str(link.get("uri")) for link in p.get_links()]
    parts += [str(doc.metadata), str(doc.get_toc()), str(doc.embfile_names())]
    parts += [doc.embfile_get(n).decode("utf-8", "replace") for n in doc.embfile_names()]
    return "\n".join(parts)


def test_text_truly_removed(synth, harvested):
    people, src, out, ctx = _scrub(synth, harvested)
    doc = pymupdf.open(stream=out, filetype="pdf")
    assert_no_secrets(_everything(doc), people, "in PDF")
    raw = b"".join(doc.xref_stream(x) or b"" for x in range(1, doc.xref_length()) if doc.xref_is_stream(x))
    for p in people:
        for v in (p.ssn, p.mrn, p.email, p.last):
            assert v.encode() not in raw
    assert ctx.tally.issues == []


def test_tokens_drawn_in_place_and_file_valid(synth, harvested):
    people, src, out, ctx = _scrub(synth, harvested)
    a, b = pymupdf.open(stream=src, filetype="pdf"), pymupdf.open(stream=out, filetype="pdf")
    assert a.page_count == b.page_count
    text = b[0].get_text(sort=True)
    assert re.search(r"Visit summary for\s+PERSON_\d{4}", text)
    assert re.search(r"MRN:\s+MRN_\d{4}", text)


def test_metadata_annotations_fields_links_scrubbed(synth, harvested):
    people, src, out, ctx = _scrub(synth, harvested)
    doc = pymupdf.open(stream=out, filetype="pdf")
    assert not any(v for k, v in doc.metadata.items() if k not in ("format", "encryption"))
    annot = next(doc[0].annots())
    assert re.fullmatch(r"PERSON_\d{4}", annot.info["title"]) and "PHONE_" in annot.info["content"]
    assert re.fullmatch(r"PERSON_\d{4}", next(doc[0].widgets()).field_value)
    # a link over redacted text is removed with it; any that remain point nowhere real
    assert all(link["uri"].startswith("https://scrubbed.invalid/URL_") for link in doc[0].get_links() if link.get("uri"))
    assert "PERSON_" in doc.get_toc()[0][1]
    (name,) = doc.embfile_names()
    assert people[1].last not in name
    assert "PERSON_" in doc.embfile_get(name).decode()


def test_scanned_pdf_rebuilt_from_redacted_images(ctx, synth):
    import pytesseract
    from PIL import Image

    from fixtures import synth_images as si
    p = synth.person()
    src = si.scanned_pdf([si.text_image(si.note_lines(synth, p)), si.id_badge(p)])
    doc = pymupdf.open(stream=scrub_pdf_bytes(src, ctx, "scan.pdf"), filetype="pdf")
    assert doc.page_count == 2 and not any(v for k, v in doc.metadata.items() if k not in ("format", "encryption"))
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        text = pytesseract.image_to_string(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        for s in (p.last, p.mrn, p.ssn, p.email):
            assert s not in text
    assert ctx.tally.counts["FACE_IMAGE"] == 1
    assert ctx.tally.issues == []


def test_blank_scan_page_still_rebuilt(ctx):
    doc = pymupdf.open(stream=scrub_pdf_bytes(scanned_pdf_bytes(), ctx, "blank.pdf"), filetype="pdf")
    assert doc.page_count == 1
