"""FHIR (HL7) JSON: the format EHRs export patient records in (Bundles of Patient, Encounter,
Observation, Claim ... resources).

The structure says what every field is, so most of the work needs no guessing:
  - codes and their descriptions (SNOMED, LOINC, RxNorm "display"/"text") are kept: they are the
    clinical content, not identifiers
  - names, addresses, telecom, identifiers and dates are replaced by rule (tokens, years, ZIP3)
  - geolocation extensions (latitude/longitude of the home address) are zeroed
  - resource ids and references get consistent opaque UUIDs, so a patient's encounters, labs
    and claims still point at each other
  - generated narratives (text.div) are replaced; embedded plain-text notes (attachment.data)
    are decoded, scrubbed and re-encoded
Only free text (notes, comments, reference labels) goes to the language model, after every
name found in the record has been replaced word for word (Synthea names carry digits,
"Humberto482", which the model would miss).
"""

from __future__ import annotations

import base64
import re
from typing import Any

from .base import Context

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ISO_DATE = re.compile(r"^(\d{4})(-\d{2}(-\d{2}(T[\d:.]+(Z|[+-]\d{2}:\d{2})?)?)?)?$")
COND_REF = re.compile(r"^(\w+)\?identifier=([^|]*)\|(.+)$")
IDENT_SEARCH = re.compile(r"identifier=([^|&]*)\|([^&]+)")
NARRATIVE = '<div xmlns="http://www.w3.org/1999/xhtml">[narrative removed by the DataFrenzy Scrubber]</div>'
KEEP = {"resourceType", "system", "code", "unit", "version", "status", "use", "gender", "language", "comparator",
        "intent", "priority", "mode", "outcome", "contentType", "currency", "method", "valueCode", "profile",
        "sopClassUid", "modality", "lastUpdated_kept"}
TERMINOLOGY_PARENTS = {"coding", "valueCoding"}
PERSON_RESOURCES = {"Patient", "Practitioner", "RelatedPerson", "Person"}
ORG_RESOURCES = {"Organization", "Location", "HealthcareService", "InsurancePlan"}
GEO = "geolocation"


def is_fhir(obj: Any) -> bool:
    return isinstance(obj, dict) and isinstance(obj.get("resourceType"), str) and (
        "entry" in obj or "id" in obj or "meta" in obj)


class FhirScrubber:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.names: dict[str, str] = {}         # name text found in the record -> PERSON token
        self.pending: list[tuple[Any, Any, str, bool]] = []   # (container, key, text, is_base64)
        self.kept: set[str] = set()
        self.types: dict[str, str] = {}         # resource id / fullUrl -> resourceType

    # ------------------------------------------------------------ helpers
    def token(self, etype: str, value: str) -> str:
        self.ctx.tally.counts[etype] += 1
        return self.ctx.pseudo.token(etype, value)

    def uuid(self, value: str) -> str:
        h = self.ctx.pseudo.opaque_id("fhir:" + value.lower(), 32)
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def ids(self, value: str) -> str:
        return UUID.sub(lambda m: self.uuid(m.group(0)), value)

    def year(self, value: str, birth: bool = False) -> str:
        m = ISO_DATE.match(value)
        y = int(m.group(1))
        self.ctx.tally.years.add(y)
        if m.group(2):
            self.ctx.tally.counts["DATE_BIRTH" if birth else "DATE"] += 1
        if birth and self.ctx.gen.reference_year - y > 89:
            self.ctx.tally.counts["AGE_OVER_89"] += 1
            return "1900"   # 90 or older: year withheld (FHIR dates must stay dates)
        return str(y)

    def person(self, text: str) -> str:
        key = " ".join(text.split())
        if key not in self.names:
            self.names[key] = self.token("PERSON", key)
        return self.names[key]

    def free(self, container, key, text: str, b64: bool = False) -> None:
        self.pending.append((container, key, text, b64))

    # ------------------------------------------------------------ pass 1: collect names
    def collect(self, node: Any, rtype: str | None = None) -> None:
        if isinstance(node, dict):
            rtype = node.get("resourceType", rtype)
            if isinstance(node.get("resourceType"), str) and isinstance(node.get("id"), str):
                self.types[node["id"]] = node["resourceType"]
                self.types[f"urn:uuid:{node['id']}"] = node["resourceType"]
            for k, v in node.items():
                if k == "name" and isinstance(v, list) and rtype in PERSON_RESOURCES:
                    for hn in v:
                        if isinstance(hn, dict):
                            given = [g for g in hn.get("given", []) if isinstance(g, str)]
                            family = _family(hn)
                            full = " ".join(given + [family]).strip()
                            if full:
                                tok = self.person(full)
                                for part in given + family.split():
                                    if len(part) > 1:
                                        self.names.setdefault(part, tok)
                elif k == "valueString" and "mothersMaidenName" in str(node.get("url", "")):
                    self.person(v)
                else:
                    self.collect(v, rtype)
        elif isinstance(node, list):
            for v in node:
                self.collect(v, rtype)

    # ------------------------------------------------------------ pass 2: rules
    def walk(self, node: Any, key: str = "", parent_key: str = "", rtype: str | None = None,
             ext_url: str = "") -> Any:
        if isinstance(node, dict):
            rtype = node.get("resourceType", rtype)
            url = node.get("url", "") if isinstance(node.get("url"), str) else ""
            ext = f"{ext_url}|{url}" if key in ("extension", "modifierExtension") else ext_url
            if key == "text" and "div" in node:          # generated narrative
                node["div"] = NARRATIVE
            if isinstance(node.get("display"), str) and (
                    isinstance(node.get("reference"), str) or set(node) <= {"display", "identifier", "type"}):
                self.reference_display(node)          # a Reference: its label names a person or organization
            elif isinstance(node.get("display"), str) and "code" in node and "system" in node:
                self.kept.add(node["display"])        # a Coding outside "coding" (ImagingStudy.modality ...)
            if "coding" in node and isinstance(node.get("text"), str):
                self.kept.add(node["text"])           # CodeableConcept.text = code description
            if key in ("name",) and rtype in PERSON_RESOURCES and ("family" in node or "given" in node):
                return self.human_name(node)
            if key in ("address", "valueAddress"):
                return self.address(node, ext)
            if key == "telecom" or parent_key == "telecom":
                return self.telecom(node)
            if "data" in node and "contentType" in node and isinstance(node.get("data"), str):
                self.attachment(node)
            for k in list(node):
                if k in ("data",) and "contentType" in node:
                    continue
                node[k] = self.walk(node[k], k, key, rtype, ext)
            return node
        if isinstance(node, list):
            for i, v in enumerate(node):
                node[i] = self.walk(v, key, parent_key, rtype, ext_url)
            return node
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            if GEO in ext_url and key in ("valueDecimal", "latitude", "longitude"):
                self.ctx.tally.counts["LOCATION"] += 1
                return 0.0
            return node
        if isinstance(node, str):
            return self.string(node, key, parent_key, rtype, ext_url)
        return node

    def string(self, value: str, key: str, parent_key: str, rtype: str | None, ext_url: str) -> str:
        if not value.strip():
            return value
        if key in KEEP or key == "url":
            return value
        if parent_key in TERMINOLOGY_PARENTS and key in ("display", "code", "system", "version"):
            self.kept.add(value)
            return value
        if key in ("id", "fullUrl") or (key == "reference" and not COND_REF.match(value)):
            return self.ids(value)
        if key == "reference":
            m = COND_REF.match(value)
            return f"{m.group(1)}?identifier={m.group(2)}|{self.token('OTHER_ID', m.group(3))}"
        if "identifier=" in value and "|" in value:   # request.ifNoneExist / request.url searches
            return IDENT_SEARCH.sub(lambda m: f"identifier={m.group(1)}|{self.token('OTHER_ID', m.group(2))}", value)
        if key == "value" and parent_key == "identifier":
            if re.fullmatch(r"\d{3}-\d{2}-\d{4}", value):
                return self.token("US_SSN", value)
            return self.ids(value) if UUID.fullmatch(value) else self.token("OTHER_ID", value)
        if key == "uid":
            return self.token("OTHER_ID", value)
        if ISO_DATE.match(value):
            return self.year(value, birth=key in ("birthDate",))
        if key == "valueString" and "mothersMaidenName" in ext_url:
            return self.person(value)
        if key == "valueString" and ("us-core-race" in ext_url or "us-core-ethnicity" in ext_url):
            return value
        if key == "name" and rtype in ORG_RESOURCES:
            return self.token("ORGANIZATION", value)
        if key in ("display", "text", "valueString", "description", "comment", "note", "name", "title",
                   "conclusion", "patientInstruction", "summary", "detail"):
            if value in self.kept:
                return value
            from .tabular import is_terminology
            if is_terminology(value):
                self.kept.add(value)
                return value
            self.free(None, None, value)
            return _Pending(len(self.pending) - 1)
        if UUID.search(value):
            return self.ids(value)
        self.free(None, None, value)
        return _Pending(len(self.pending) - 1)

    def reference_display(self, ref: dict) -> None:
        """Reference.display is the referenced resource's name: a person or an organization."""
        target = ref.get("reference") if isinstance(ref.get("reference"), str) else ""
        display = ref["display"]
        rtype = self.types.get(target) or target.split("/", 1)[0].split("?", 1)[0]
        if not rtype:   # a label without a link: a title means a person, anything else an organization
            rtype = "Practitioner" if re.match(r"^(Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s", display) else "Organization"
        if rtype in PERSON_RESOURCES:
            m = re.match(r"^((?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+)?(.*)$", display)
            ref["display"] = (m.group(1) or "") + self.person(m.group(2))
        elif rtype in ORG_RESOURCES:
            ref["display"] = self.token("ORGANIZATION", display)
        else:
            return
        self.kept.add(ref["display"])

    def human_name(self, hn: dict) -> dict:
        given = [g for g in hn.get("given", []) if isinstance(g, str)]
        family = _family(hn)
        tok = self.person(" ".join(given + [family]).strip() or hn.get("text", ""))
        if isinstance(hn.get("family"), list):         # FHIR DSTU2: family is a list
            hn["family"] = [tok for _ in hn["family"]]
        elif "family" in hn:
            hn["family"] = tok
        if "given" in hn:
            hn["given"] = [tok for _ in hn["given"]]
        if isinstance(hn.get("text"), str):
            hn["text"] = tok
        for k in ("period",):
            if k in hn:
                hn[k] = self.walk(hn[k], k)
        return hn                                    # prefix/suffix (Mr., Dr., MD) stay

    def address(self, node: Any, ext: str) -> Any:
        if not isinstance(node, dict):
            return node
        if isinstance(node.get("line"), list):
            node["line"] = [self.token("STREET_ADDRESS", ln) if isinstance(ln, str) and ln.strip() else ln
                            for ln in node["line"]]
        for k in ("city", "district"):
            if isinstance(node.get(k), str) and node[k].strip():
                node[k] = self.token("LOCATION", node[k])
        for k in ("state", "country"):              # state and country may stay (Safe Harbor)
            if isinstance(node.get(k), str):
                self.kept.add(node[k])
        if isinstance(node.get("postalCode"), str) and node["postalCode"].strip():
            self.ctx.tally.counts["ZIP_CODE"] += 1
            node["postalCode"] = self.ctx.gen.zip_text(node["postalCode"])
        if isinstance(node.get("text"), str):
            node["text"] = self.token("STREET_ADDRESS", node["text"])
        for k in ("extension", "period"):
            if k in node:
                node[k] = self.walk(node[k], k, "", None, ext)
        return node

    def telecom(self, node: Any) -> Any:
        if isinstance(node, dict) and isinstance(node.get("value"), str):
            sys_ = node.get("system", "")
            if sys_ == "email" or "@" in node["value"]:
                self.ctx.tally.counts["EMAIL_ADDRESS"] += 1
                node["value"] = self.ctx.pseudo.email_address(node["value"])
            elif sys_ == "url":
                node["value"] = f"https://scrubbed.invalid/{self.token('URL', node['value'])}"
            else:
                node["value"] = self.token("FAX_NUMBER" if sys_ == "fax" else "PHONE_NUMBER", node["value"])
        if isinstance(node, dict) and "period" in node:
            node["period"] = self.walk(node["period"], "period")
        return node

    def attachment(self, node: dict) -> None:
        ctype = str(node.get("contentType", ""))
        try:
            raw = base64.b64decode(node["data"], validate=False)
        except Exception:
            raw = b""
        if ctype.startswith("text/") and raw:
            self.free(node, "data", raw.decode("utf-8", errors="replace"), True)
        else:
            # pictures, PDFs and other binaries inside a FHIR record are not exported
            self.ctx.quarantine(f"{self.ctx.out_rel} attachment ({ctype or 'unknown type'})",
                                "binary attachment inside a FHIR record is removed")
            node["data"] = ""

    # ------------------------------------------------------------ free text
    def _known_names(self, text: str) -> str:
        for name in sorted(self.names, key=len, reverse=True):
            if name in text:
                text = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", self.names[name], text)
        return text

    def flush(self, root: Any) -> Any:
        if not self.pending:
            return root
        texts = [self._known_names(t) for _, _, t, _ in self.pending]
        results = self.ctx.scrubber.scrub_joined(texts, self.ctx.tally)
        values = []
        for (container, key, _, b64), res in zip(self.pending, results):
            if container is not None:
                container[key] = base64.b64encode(res.text.encode("utf-8")).decode("ascii") if b64 else res.text
            values.append(res.text)
        self.pending = []
        return _resolve(root, values)


def _family(hn: dict) -> str:
    f = hn.get("family")
    if isinstance(f, list):
        return " ".join(x for x in f if isinstance(x, str))
    return f if isinstance(f, str) else ""


class _Pending(int):
    pass


def _resolve(node: Any, values: list[str]) -> Any:
    if isinstance(node, _Pending):
        return values[int(node)]
    if isinstance(node, dict):
        for k in node:
            node[k] = _resolve(node[k], values)
    elif isinstance(node, list):
        for i in range(len(node)):
            node[i] = _resolve(node[i], values)
    return node


def scrub_fhir_obj(obj: Any, ctx: Context) -> tuple[Any, set[str]]:
    fs = FhirScrubber(ctx)
    fs.collect(obj)
    out = fs.flush(fs.walk(obj))
    return out, fs.kept


def residual_strings(obj: Any, kept: set[str], key: str = "", parent_key: str = "") -> list[str]:
    """Strings the double-check should read again: free text, not codes, ids, URLs or dates."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += residual_strings(v, kept, k, key)
    elif isinstance(obj, list):
        for v in obj:
            out += residual_strings(v, kept, key, parent_key)
    elif isinstance(obj, str) and obj.strip():
        if key in KEEP or key in ("url", "id", "fullUrl", "reference", "div", "data", "uid", "ifNoneExist") or obj in kept:
            return out
        if parent_key in TERMINOLOGY_PARENTS or ISO_DATE.match(obj) or UUID.fullmatch(obj):
            return out
        out.append(obj)
    return out
