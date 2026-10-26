"""HL7 v2 messages (.hl7, .er7, or text starting with "MSH|"): ADT, ORU, ORM, SIU ... the
pipe-delimited messages hospital systems send each other.

The HL7 standard fixes what each field holds (PID-5 patient name, PID-7 birth date, PID-11
address, PID-19 SSN ...), so fields are replaced by rule, component by component:
  names (XPN/XCN)      -> one PERSON token per person (an XCN's ID becomes an ID token)
  addresses (XAD)      -> street/city/county tokens, ZIP -> ZIP3, state/country kept
  phone/email (XTN)    -> tokens
  IDs (CX, ST ids)     -> identifier tokens (SSN, account, licence ... by field)
  timestamps (TS/DTM)  -> year
  facilities           -> ORGANIZATION tokens
Codes (CE/CWE: code^text^system), units, statuses and results stay. Free text (OBX-5 text
values, NTE comments) is read by the language model after the message's own names are
replaced word for word. Separators, segment and field counts never change.
"""

from __future__ import annotations

import re

from .base import Context

TS = re.compile(r"^(\d{4})(\d{2}(\d{2}(\d{2}(\d{2}(\d{2}(\.\d+)?)?)?)?)?)?([+-]\d{4})?$")

# segment -> {field number: kind}
FIELDS: dict[str, dict[int, str]] = {
    "MSH": {4: "org", 6: "org", 7: "ts"},
    "EVN": {2: "ts", 3: "ts", 5: "xcn", 6: "ts"},
    "PID": {8: "code", 10: "code", 15: "code", 16: "code", 17: "code", 22: "code", 24: "code", 25: "code",
            28: "code", 30: "code", 2: "id", 3: "id", 4: "id", 5: "xpn", 6: "xpn", 7: "dob", 9: "xpn", 11: "xad", 12: "place",
            13: "xtn", 14: "xtn", 18: "account", 19: "ssn", 20: "licence", 21: "id", 23: "place", 29: "ts"},
    "PD1": {3: "org", 4: "xcn"},
    "MRG": {1: "id", 2: "id", 3: "account", 4: "id", 5: "id", 7: "xpn"},
    "NK1": {3: "code", 7: "code", 14: "code", 15: "code", 2: "xpn", 4: "xad", 5: "xtn", 6: "xtn", 13: "org", 16: "dob", 30: "xpn", 31: "xtn", 32: "xad",
            33: "id", 37: "ssn"},
    "PV1": {2: "code", 4: "code", 10: "code", 14: "code", 18: "code", 36: "code", 41: "code", 3: "location", 7: "xcn", 8: "xcn", 9: "xcn", 17: "xcn", 19: "id", 44: "ts", 45: "ts", 50: "id",
            52: "xcn"},
    "PV2": {8: "ts", 9: "ts", 11: "ts"},
    "ORC": {2: "id", 3: "id", 4: "id", 9: "ts", 10: "xcn", 11: "xcn", 12: "xcn", 15: "ts", 17: "org",
            19: "xcn", 21: "org", 22: "xad", 23: "xtn", 24: "xad"},
    "OBR": {4: "code", 24: "code", 25: "code", 2: "id", 3: "id", 6: "ts", 7: "ts", 8: "ts", 14: "ts", 16: "xcn", 17: "xtn", 20: "id", 21: "id",
            22: "ts", 28: "xcn", 32: "xcn", 33: "xcn", 34: "xcn", 35: "xcn", 36: "ts"},
    "OBX": {2: "code", 3: "code", 4: "code", 5: "value", 6: "code", 7: "code", 8: "code", 11: "code", 12: "ts",
            14: "ts", 16: "xcn", 17: "code", 19: "ts", 23: "org", 24: "xad", 25: "xcn"},
    "NTE": {3: "text"},
    "AL1": {2: "code", 3: "code", 4: "code", 5: "code", 6: "ts"},
    "DG1": {2: "code", 3: "code", 4: "code", 5: "ts", 6: "code", 16: "xcn", 19: "ts"},
    "PR1": {2: "code", 3: "code", 4: "code", 5: "ts", 6: "code", 11: "xcn", 12: "xcn"},
    "GT1": {9: "code", 10: "code", 11: "code", 2: "id", 3: "xpn", 4: "xpn", 5: "xad", 6: "xtn", 7: "xtn", 8: "dob", 12: "ssn", 16: "org",
            17: "xad", 18: "xtn", 19: "id"},
    "IN1": {15: "code", 17: "code", 2: "id", 3: "id", 4: "org", 5: "xad", 6: "xpn", 7: "xtn", 8: "id", 9: "org", 12: "ts", 13: "ts",
            16: "xpn", 18: "dob", 19: "xad", 36: "policy", 49: "id"},
    "IN2": {1: "id", 2: "ssn", 6: "id", 8: "id"},
    "SCH": {1: "id", 2: "id", 11: "ts", 12: "xcn", 13: "xtn", 16: "xcn", 17: "xtn", 20: "xcn", 21: "xtn"},
    "TXA": {4: "ts", 5: "xcn", 6: "ts", 7: "ts", 9: "xcn", 10: "xcn", 11: "xcn", 12: "id", 22: "xcn"},
    "RXA": {3: "ts", 4: "ts", 5: "code", 6: "code", 7: "code", 9: "code", 10: "xcn", 15: "id", 17: "code"},
    "RXE": {2: "code", 3: "code", 5: "code", 6: "code", 13: "xcn"},
    "RXO": {1: "code", 14: "xcn"},
    "BHS": {4: "org", 6: "org", 7: "ts", 11: "id"},
    "FHS": {4: "org", 6: "org", 7: "ts", 11: "id"},
    "SPM": {2: "id", 17: "ts", 18: "ts"},
}
ID_ENTITY = {"id": "OTHER_ID", "account": "ACCOUNT_NUMBER", "ssn": "US_SSN", "licence": "LICENSE_NUMBER",
             "policy": "HEALTH_PLAN_ID"}
TEXT_TYPES = {"TX", "ST", "FT"}


def looks_like_hl7(head: bytes) -> bool:
    head = head.lstrip(b"\xef\xbb\xbf\x0b \r\n")
    return head[:3] in (b"MSH", b"BHS", b"FHS") and len(head) > 8 and not head[3:4].isalnum()


class Hl7Scrubber:
    def __init__(self, ctx: Context, enc: dict):
        self.ctx = ctx
        self.e = enc
        self.names: dict[str, str] = {}
        self.pending: list[tuple[list, int, str]] = []   # (container list, index, text)
        self.kept: set[str] = set()

    def token(self, etype: str, value: str) -> str:
        self.ctx.tally.counts[etype] += 1
        return self.ctx.pseudo.token(etype, value)

    def person(self, full: str) -> str:
        key = " ".join(full.split())
        if key not in self.names:
            self.names[key] = self.token("PERSON", key)
        return self.names[key]

    # ------------------------------------------------------------ data types
    def year(self, value: str, birth: bool = False) -> str:
        m = TS.match(value.strip())
        if m and not 1850 <= int(m.group(1)) <= 2100:
            m = None                                       # "10131113" is not the year 1013
        if not m:
            # a malformed date in a date field ("196203520") still keeps only its year
            m2 = re.match(r"^\s*((?:18|19|20)\d\d)\d", value)
            if not m2:
                return self.token("OTHER_ID", value) if re.search(r"\d{4}", value) else value
            m = TS.match(m2.group(1))
        y = int(m.group(1))
        self.ctx.tally.years.add(y)
        if m.group(2):
            self.ctx.tally.counts["DATE_BIRTH" if birth else "DATE"] += 1
        if birth and self.ctx.gen.reference_year - y > 89:
            self.ctx.tally.counts["AGE_OVER_89"] += 1
            return "1900"
        return str(y)

    def xpn(self, comps: list[str]) -> list[str]:
        """Family^Given^Middle^Suffix^Prefix^Degree: one token for the person, titles kept."""
        family = comps[0].split(self.e["sub"])[0] if comps else ""
        parts = [p for p in [comps[1] if len(comps) > 1 else "", comps[2] if len(comps) > 2 else "", family] if p]
        if not parts:
            return comps
        tok = self.person(" ".join(parts))
        out = list(comps)
        for i in (0, 1, 2):
            if i < len(out) and out[i]:
                out[i] = tok
        return out

    def xcn(self, comps: list[str]) -> list[str]:
        """ID^Family^Given^Middle^Suffix^Prefix^Degree^..."""
        out = list(comps)
        if out and out[0]:
            out[0] = self.token("OTHER_ID", out[0])
        if len(out) > 1:
            out[1:] = self.xpn(out[1:])
        if len(out) > 8 and re.search(r"[A-Za-z]{2}", out[8]):
            out[8] = self.token("ORGANIZATION", out[8])     # the assigning authority / practice
        return out

    def xad(self, comps: list[str]) -> list[str]:
        """Street^Other^City^State^ZIP^Country^Type^Other geo^County"""
        out = list(comps)
        for i, etype in ((0, "STREET_ADDRESS"), (1, "STREET_ADDRESS"), (2, "LOCATION"), (7, "LOCATION"),
                         (8, "LOCATION")):
            if i < len(out) and out[i].strip():
                out[i] = self.token(etype, out[i].split(self.e["sub"])[0])
        if len(out) > 4 and out[4].strip():
            self.ctx.tally.counts["ZIP_CODE"] += 1
            out[4] = self.ctx.gen.zip_text(out[4])
        for i in (3, 5):
            if i < len(out) and out[i].strip():
                self.kept.add(out[i])
        return out

    def xtn(self, comps: list[str]) -> list[str]:
        """Number^Use^Equipment^Email^Country^Area^Local^Extension^..."""
        out = list(comps)
        if out and out[0].strip():
            v = out[0]
            out[0] = (self.ctx.pseudo.email_address(v) if "@" in v else self.token("PHONE_NUMBER", v))
        if len(out) > 3 and "@" in out[3]:
            self.ctx.tally.counts["EMAIL_ADDRESS"] += 1
            out[3] = self.ctx.pseudo.email_address(out[3])
        for i in (5, 6, 7):
            if i < len(out) and out[i].strip():
                out[i] = self.token("PHONE_NUMBER", out[i])
        return out

    def ident(self, comps: list[str], kind: str) -> list[str]:
        """CX: ID^check digit^scheme^assigning authority^type ... the ID itself is replaced."""
        out = list(comps)
        if out and out[0].strip():
            out[0] = self.token(ID_ENTITY.get(kind, "OTHER_ID"), out[0])
        for i in range(1, len(out)):
            if re.fullmatch(r"(?:18|19|20)\d{6}\d*", out[i] or ""):
                out[i] = self.year(out[i])                # an expiry / issue date inside the ID
        return out

    # ------------------------------------------------------------ fields
    def field(self, seg: str, num: int, value: str, obx_type: str = ""):
        """The scrubbed field; free text comes back as a holder filled in by flush()."""
        if not value:
            return value
        kind = FIELDS.get(seg, {}).get(num)
        if kind is None:
            return self.unmapped(seg, value)
        if kind == "text" or (kind == "value" and obx_type in TEXT_TYPES):
            return self.free_field(value)
        if kind in ("value", "code"):
            for piece in re.split(r"[\^~&]", value):
                if piece.strip():
                    self.kept.add(piece)
            return value                                    # codes, units, flags, numeric/coded results stay
        e = self.e
        out_reps = []
        for rep in value.split(e["rep"]):
            comps = rep.split(e["comp"])
            if kind in ("ts", "dob"):
                comps[0] = self.year(comps[0], birth=(kind == "dob"))
            elif kind == "xpn":
                comps = self.xpn(comps)
            elif kind == "xcn":
                comps = self.xcn(comps)
            elif kind == "xad":
                comps = self.xad(comps)
            elif kind == "xtn":
                comps = self.xtn(comps)
            elif kind in ID_ENTITY:
                comps = self.ident(comps, kind)
            elif kind == "org":
                if comps[0].strip():
                    comps[0] = self.token("ORGANIZATION", comps[0])
            elif kind == "place":
                if comps[0].strip():
                    comps[0] = self.token("LOCATION", comps[0])
            elif kind == "location":
                for i in (3, 8):   # PL: point of care^room^bed^FACILITY^...^description
                    if i < len(comps) and comps[i].strip():
                        comps[i] = self.token("ORGANIZATION", comps[i])
            out_reps.append(e["comp"].join(comps))
        return e["rep"].join(out_reps)

    def unmapped(self, seg: str, value: str):
        """A field the map doesn't cover (systems misplace fields; Z segments carry anything).
        Shaped like a person (ID^SURNAME^GIVEN...) it is one; any other text is read component by
        component; plain numbers, codes and flags stay."""
        if seg in ("MSH", "BTS", "FTS"):
            return value
        e = self.e
        if TS.fullmatch(value.split(e["comp"])[0]) and len(value.split(e["comp"])[0]) >= 8:
            comps = value.split(e["comp"])
            comps[0] = self.year(comps[0])                 # a timestamp wherever it sits
            return e["comp"].join(comps)
        if not re.search(r"[A-Za-z]", value):
            return self.free_field(value) if re.search(r"\d{5}", value) else value
        first = value.split(e["rep"])[0].split(e["comp"])
        if len(first) >= 2 and re.fullmatch(r"[A-Za-z]{0,4}\d[\w.:-]{0,19}", first[0] or "") \
                and not re.fullmatch(r"\d{3}-\d{2}-\d{4}", first[0]) and re.match(r"[A-Za-z]", first[1] or "") \
                and not re.fullmatch(r"[A-Za-z][A-Za-z' -]+", first[2] if len(first) > 2 and first[2] else "x1"):
            for piece in re.split(r"[\^~&]", value):   # a code^description(^system): terminology
                if piece.strip():
                    self.kept.add(piece)
            return value
        reps = []
        for rep in value.split(e["rep"]):
            comps = rep.split(e["comp"])
            if (len(comps) >= 3 and re.fullmatch(r"[A-Za-z][A-Za-z' -]+", comps[1] or "-")
                    and re.fullmatch(r"[A-Za-z][A-Za-z' .-]*", comps[2] or "-") and (comps[0] == "" or re.search(r"\d", comps[0]))):
                reps.append(e["comp"].join(self.xcn(comps)))
            elif len(comps) >= 2 and re.fullmatch(r"[A-Za-z][A-Za-z' -]{3,}", comps[0]) \
                    and re.fullmatch(r"[A-Za-z][A-Za-z' .-]*", comps[1]) and seg in ("PID", "NK1", "GT1", "IN1", "PV1", "PD1"):
                reps.append(e["comp"].join(self.xpn(comps)))
            else:
                reps.append(rep)
        joined = e["rep"].join(reps)
        if joined != value:
            return joined
        return self.free_field(value) if re.search(r"[A-Za-z]{2}|\d{5}", value) else value

    def free_field(self, value: str) -> str:
        holder = [value]
        self.pending.append((holder, 0, value))
        return holder

    # ------------------------------------------------------------ message
    def collect(self, segments: list[list[str]]) -> None:
        """Names first, so the same people are caught in comments and text results."""
        e = self.e
        for f in segments:
            seg = f[0]
            for num, kind in FIELDS.get(seg, {}).items():
                if kind not in ("xpn", "xcn") or num >= len(f):
                    continue
                for rep in f[num].split(e["rep"]):
                    comps = rep.split(e["comp"])
                    if kind == "xcn":
                        comps = comps[1:]
                    family = comps[0].split(e["sub"])[0] if comps else ""
                    given = [c for c in comps[1:3] if c]
                    full = " ".join(given + ([family] if family else []))
                    if full:
                        tok = self.person(full)
                        for p in given + [family]:
                            if len(p) > 2:
                                self.names.setdefault(p, tok)
                                self.names.setdefault(p.title(), tok)

    def flush(self) -> None:
        if not self.pending:
            return
        from .tabular import is_terminology

        def known(t: str) -> str:
            for name in sorted(self.names, key=len, reverse=True):
                if name in t:
                    t = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", self.names[name], t)
            return t
        todo = [(h, i, t) for h, i, t in self.pending if not is_terminology(t)]
        results = self.ctx.scrubber.scrub_joined([known(t) for _, _, t in todo], self.ctx.tally) if todo else []
        for (holder, i, _), res in zip(todo, results):
            holder[i] = res.text
        self.pending = []


def _split(data: str) -> tuple[list[str], str]:
    newline = "\r\n" if "\r\n" in data else ("\r" if "\r" in data else "\n")
    return re.split(r"\r\n|\r|\n", data), newline


def scrub_hl7_text(text: str, ctx: Context, item: str) -> str:
    lines, newline = _split(text)
    first = next((ln for ln in lines if ln.strip()), "")
    start = first.lstrip("﻿\x0b ")
    if len(start) < 8:
        return text
    fsep = start[3]
    enc = {"field": fsep, "comp": start[4], "rep": start[5], "esc": start[6], "sub": start[7]}
    hs = Hl7Scrubber(ctx, enc)
    parsed = [ln.split(fsep) if ln.strip() else None for ln in lines]
    segments = [p for p in parsed if p]
    hs.collect(segments)
    out_lines: list = []
    for p, raw in zip(parsed, lines):
        if not p:
            out_lines.append(raw)
            continue
        seg = p[0].lstrip("﻿\x0b ")[-3:]
        obx_type = p[2] if seg == "OBX" and len(p) > 2 else ""
        fields: list = [p[0]]
        for idx in range(1, len(p)):
            # MSH-1 is the separator itself, so MSH field numbers are shifted by one
            num = idx + 1 if seg in ("MSH", "BHS", "FHS") else idx
            if seg in ("MSH", "BHS", "FHS") and idx == 1:
                fields.append(p[idx])                     # encoding characters
                continue
            fields.append(hs.field(seg, num, p[idx], obx_type))
        out_lines.append(fields)
    hs.flush()
    rendered = []
    for f in out_lines:
        if isinstance(f, str):
            rendered.append(f)
        else:
            rendered.append(fsep.join(x[0] if isinstance(x, list) else x for x in f))
    out = newline.join(rendered)
    ctx.kept |= hs.kept
    _verify(text, out, ctx, item, hs)
    ctx.tally.records += sum(1 for s in segments if s[0].lstrip("﻿\x0b ").endswith("MSH"))
    return out


def _verify(before: str, after: str, ctx: Context, item: str, hs: Hl7Scrubber) -> None:
    a, _ = _split(before)
    b, _ = _split(after)
    sep = hs.e["field"]
    if len(a) != len(b) or any(x.count(sep) != y.count(sep) for x, y in zip(a, b)):
        ctx.issue(f"{item}: HL7 structure changed during scrubbing")
    from .tabular import is_terminology, needs_check
    texts = []
    for line in b:
        f = line.split(sep)
        seg = f[0][-3:] if f else ""
        if seg in ("MSH", "BHS", "FHS", "BTS", "FTS"):
            continue
        for idx, v in enumerate(f[1:], start=1):
            kind = FIELDS.get(seg, {}).get(idx)
            if kind in ("text", "value") or (seg.startswith("Z") and v):
                for piece in re.split(r"[\^~&]", v):
                    if piece.strip() and piece not in hs.kept and not is_terminology(piece) and needs_check(piece):
                        texts.append(piece)
    if texts:
        ctx.residual("\n\n".join(dict.fromkeys(texts)), item)


def scrub_hl7_bytes(data: bytes, ctx: Context, item: str) -> bytes:
    text = data.decode("utf-8", errors="replace") if not data.startswith(b"\xff\xfe") else data.decode("utf-16")
    return scrub_hl7_text(text, ctx, item).encode("utf-8")


def process_hl7(src, dst, ctx: Context) -> None:
    from pathlib import Path
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(scrub_hl7_bytes(Path(src).read_bytes(), ctx, ctx.out_rel))
