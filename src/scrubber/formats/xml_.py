"""XML, with HL7 CDA / C-CDA awareness (the XML documents EHRs exchange: CCD, discharge summary,
referral note).

Element and attribute names say what most values are, as in FHIR:
  - codes are kept: @code, @codeSystem*, @displayName (SNOMED/LOINC/RxNorm descriptions)
  - <name> (given/family/prefix/suffix) -> one PERSON token per person
  - <addr>: streetAddressLine/city/county -> tokens, postalCode -> ZIP3, state/country kept
  - @value on <telecom> (tel:, mailto:) -> tokens
  - <id @extension> -> identifier tokens (SSN root -> US_SSN); UUID @root -> consistent opaque UUID
  - HL7 timestamps (@value="20260704135757" on effectiveTime/low/high/time/birthTime) -> year
  - every other text and attribute value (narrative tables, titles, comments) is free text: names
    found in the document are replaced word for word first, then the language model reads it
Elements, attributes, namespaces and order never change.
"""

from __future__ import annotations

import io
import re
from xml.etree import ElementTree as ET

from .base import Context

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HL7_TS = re.compile(r"^(\d{4})(\d{2}(\d{2}(\d{2}(\d{2}(\d{2}(\.\d+)?)?)?)?)?)?([+-]\d{4})?$")
SSN_ROOT = "2.16.840.1.113883.4.1"
KEEP_ATTRS = {"code", "codeSystem", "codeSystemName", "codeSystemVersion", "displayName", "classCode", "moodCode",
              "typeCode", "contextControlCode", "contextConductionInd", "nullFlavor", "use", "unit", "mediaType",
              "representation", "xsi:type", "type", "inversionInd", "negationInd", "operator", "institutionSpecified",
              "assigningAuthorityName", "determinerCode", "schemaLocation", "templateId", "inclusive",
              "{http://www.w3.org/2001/XMLSchema-instance}type", "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation",
              "ID", "IDREF", "styleCode", "border", "width", "cellpadding", "cellspacing", "align", "valign"}
TIME_TAGS = {"effectiveTime", "low", "high", "center", "time", "birthTime", "deceasedTime", "signatureTime"}
NAME_PARTS = {"given", "family", "prefix", "suffix", "delimiter"}
ADDR_TOKEN = {"streetAddressLine": "STREET_ADDRESS", "houseNumber": "STREET_ADDRESS", "streetName": "STREET_ADDRESS",
              "city": "LOCATION", "county": "LOCATION", "precinct": "LOCATION", "censusTract": "LOCATION",
              "additionalLocator": "STREET_ADDRESS", "unitID": "STREET_ADDRESS", "postBox": "STREET_ADDRESS"}
ADDR_KEEP = {"state", "country"}
ORG_PARENTS = {"place", "healthCareFacility", "location", "custodian", "serviceProvider"}
PRODUCT_PARENTS = {"manufacturedMaterial", "manufacturedLabeledDrug", "playingEntity", "playingDevice",
                   "manufacturerOrganization_kept"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def is_cda(root) -> bool:
    return _local(root.tag) == "ClinicalDocument"


class XmlScrubber:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.names: dict[str, str] = {}
        self.pending: list[tuple] = []          # (element, attr or None, field "text"/"tail"/attr, text)
        self.kept: set[str] = set()

    def token(self, etype: str, value: str) -> str:
        self.ctx.tally.counts[etype] += 1
        return self.ctx.pseudo.token(etype, value)

    def uuid(self, value: str) -> str:
        h = self.ctx.pseudo.opaque_id("xml:" + value.lower(), 32)
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def person(self, full: str) -> str:
        key = " ".join(full.split())
        if key not in self.names:
            self.names[key] = self.token("PERSON", key)
        return self.names[key]

    # ------------------------------------------------------------ names first
    def _name_text(self, name_el) -> tuple[str, list[str]]:
        parts = [(c.text or "").strip() for c in name_el if _local(c.tag) in ("given", "family") and (c.text or "").strip()]
        if not parts and (name_el.text or "").strip():
            parts = [name_el.text.strip()]
        return " ".join(parts), parts

    def collect(self, root) -> None:
        for el in root.iter():
            if _local(el.tag) == "name":
                full, parts = self._name_text(el)
                if full and any(_local(c.tag) in ("given", "family") for c in el):
                    tok = self.person(full)
                    for p in parts:
                        if len(p) > 2:
                            self.names.setdefault(p, tok)

    # ------------------------------------------------------------ rules
    def walk(self, el, parent_tag: str = "", in_addr: bool = False) -> None:
        tag = _local(el.tag)
        if not isinstance(el.tag, str):        # comments / processing instructions
            if el.text and el.text.strip() and callable(el.tag) and el.tag is ET.Comment:
                self.queue(el, "text", el.text)
            return
        if tag == "name" and any(_local(c.tag) in NAME_PARTS for c in el):
            self.human_name(el)
        elif tag == "name" and (el.text or "").strip() and (
                "Organization" in parent_tag or parent_tag in ORG_PARENTS):
            el.text = self.token("ORGANIZATION", el.text.strip())   # a practice, hospital or facility
        elif tag == "name" and (el.text or "").strip() and parent_tag in PRODUCT_PARENTS:
            self.kept.add(el.text.strip())                          # a drug or device name
        elif tag in ADDR_TOKEN and in_addr and (el.text or "").strip():
            el.text = self.token(ADDR_TOKEN[tag], el.text.strip())
        elif tag == "postalCode" and (el.text or "").strip():
            self.ctx.tally.counts["ZIP_CODE"] += 1
            el.text = self.ctx.gen.zip_text(el.text.strip())
        elif tag in ADDR_KEEP and in_addr:
            if el.text:
                self.kept.add(el.text.strip())
        elif el.text and el.text.strip() and tag not in NAME_PARTS:
            self.queue(el, "text", el.text)
        for attr, value in list(el.attrib.items()):
            new = self.attribute(tag, attr, value, el)
            if new is not None and new != value:
                el.set(attr, new)
        for child in el:
            self.walk(child, tag, in_addr or tag == "addr")
            if child.tail and child.tail.strip():
                self.queue(child, "tail", child.tail)

    def human_name(self, el) -> None:
        full, _ = self._name_text(el)
        tok = self.person(full) if full else None
        for c in el:
            t = _local(c.tag)
            if t in ("given", "family") and (c.text or "").strip():
                c.text = tok
            elif t in ("prefix", "suffix"):
                if c.text:
                    self.kept.add(c.text.strip())
        if (el.text or "").strip() and tok:
            el.text = tok

    def attribute(self, tag: str, attr: str, value: str, el) -> str | None:
        name = _local(attr) if attr.startswith("{") else attr
        if not value.strip():
            return None
        if attr in KEEP_ATTRS or name in KEEP_ATTRS:
            if name == "displayName":
                self.kept.add(value)
            return None
        if name == "value" and tag in TIME_TAGS:
            m = HL7_TS.match(value.strip())
            if not m:
                return None
            y = int(m.group(1))
            self.ctx.tally.years.add(y)
            if m.group(2):
                self.ctx.tally.counts["DATE_BIRTH" if tag == "birthTime" else "DATE"] += 1
            if tag == "birthTime" and self.ctx.gen.reference_year - y > 89:
                self.ctx.tally.counts["AGE_OVER_89"] += 1
                return "1900"
            return str(y)
        if tag == "telecom" and name == "value":
            v = value.strip()
            if v.lower().startswith("mailto:"):
                self.ctx.tally.counts["EMAIL_ADDRESS"] += 1
                return "mailto:" + self.ctx.pseudo.email_address(v[7:])
            if v.lower().startswith(("http:", "https:")):
                return f"https://scrubbed.invalid/{self.token('URL', v)}"
            scheme, _, body = v.partition(":") if ":" in v else ("tel", "", v)
            etype = "FAX_NUMBER" if scheme.lower() == "fax" else "PHONE_NUMBER"
            return f"{scheme}:{self.token(etype, body)}"
        if tag == "id" and name == "extension":
            if el.get("root", "") == SSN_ROOT:
                return self.token("US_SSN", value)
            return self.uuid(value) if UUID.fullmatch(value) else self.token("OTHER_ID", value)
        if name in ("root", "extension"):
            # OIDs (2.16.840...) name code systems and templates and stay; instance UUIDs are mapped
            return UUID.sub(lambda m: self.uuid(m.group(0)), value) if UUID.search(value) else None
        if tag == "reference" and name == "value" and value.startswith("#"):
            return None                                  # points at a narrative element
        if re.fullmatch(r"[-+]?\d+(\.\d+)?", value.strip()):
            return None                                  # numbers (doses, results) stay
        self.queue(el, attr, value)
        return None

    def queue(self, el, field: str, text: str) -> None:
        self.pending.append((el, field, text))

    def _known_names(self, text: str) -> str:
        for name in sorted(self.names, key=len, reverse=True):
            if name in text:
                text = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", self.names[name], text)
        return text

    def flush(self) -> None:
        if not self.pending:
            return
        from .tabular import is_terminology
        todo = [(el, field, t) for el, field, t in self.pending if t.strip() not in self.kept and not is_terminology(t)]
        for el, field, t in self.pending:
            if is_terminology(t):
                self.kept.add(t.strip())
        texts = [self._known_names(t) for _, _, t in todo]
        results = self.ctx.scrubber.scrub_joined(texts, self.ctx.tally) if texts else []
        for (el, field, _), res in zip(todo, results):
            if field == "text":
                el.text = res.text
            elif field == "tail":
                el.tail = res.text
            else:
                el.set(field, res.text)
        self.pending = []


def _parse(data: bytes):
    """Keep namespace prefixes, comments and processing instructions as they were."""
    namespaces = []
    for event, item in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        namespaces.append(item)
    for prefix, uri in namespaces:
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    parser.feed(data)
    return parser.close()


def _shape(root) -> list:
    return [(_local(e.tag) if isinstance(e.tag, str) else "#", sorted(e.attrib)) for e in root.iter()]


def scrub_xml_bytes(data: bytes, ctx: Context, item: str) -> bytes:
    root = _parse(data)
    before = _shape(root)
    xs = XmlScrubber(ctx)
    xs.collect(root)
    xs.walk(root)
    xs.flush()
    ctx.kept |= xs.kept
    out = io.BytesIO()
    first = re.search(rb"<(?![?!])", data)               # where the root element starts
    prolog = data[:first.start()] if first else b""
    # keep the XML declaration and processing instructions (stylesheets); drop comments there
    out.write(re.sub(rb"<!--.*?-->", b"", prolog, flags=re.S))
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=False, short_empty_elements=True)
    result = out.getvalue()
    _verify(result, before, ctx, item, xs.kept)
    ctx.tally.records += 1
    return result


def _verify(result: bytes, before: list, ctx: Context, item: str, kept: set[str]) -> None:
    try:
        root = _parse(result)
    except ET.ParseError as exc:
        ctx.issue(f"{item}: scrubbed XML does not parse ({exc.__class__.__name__})")
        return
    if _shape(root) != before:
        ctx.issue(f"{item}: XML structure changed during scrubbing")
    from .tabular import is_terminology, needs_check
    texts = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for t in (el.text, el.tail):
            if t and t.strip() and t.strip() not in kept and not is_terminology(t) and needs_check(t):
                texts.append(t.strip())
        for attr, v in el.attrib.items():
            name = _local(attr) if attr.startswith("{") else attr
            if name in KEEP_ATTRS or name in ("root", "extension", "value") or v.strip() in kept:
                continue
            if needs_check(v):
                texts.append(v)
    for i in range(0, len(texts), 2000):
        ctx.residual("\n\n".join(dict.fromkeys(texts[i:i + 2000])), item)


def process_xml(src, dst, ctx: Context) -> None:
    from pathlib import Path
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out = scrub_xml_bytes(Path(src).read_bytes(), ctx, ctx.out_rel)
    dst.write_bytes(out)
