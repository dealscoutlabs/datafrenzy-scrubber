"""CSV/TSV and XLSX scrubbing with column-level rules.

Columns, headers, row counts, sheet names (unless a name itself is an
identifier) and cell formatting are preserved. CSV is streamed in batches and
checkpointed so multi-GB files resume after an interruption.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from pathlib import Path
from typing import BinaryIO, Callable

from .base import Context
from .columns import is_category, COLUMN_ENTITY, classify_by_content, classify_header

BATCH_ROWS = 500
RESIDUAL_CHARS = 50_000
EMAIL_IN_CELL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
csv.field_size_limit(1 << 30)


# ---------------------------------------------------------------- column plan

class ColumnPlan:
    def __init__(self, headers: list, samples: list[list], ctx: Context, file_name: str,
                 all_rows: list[list] | None = None):
        tab = ctx.cfg["tabular"]
        rules = tab["column_rules"]
        overrides = (tab.get("column_overrides") or {}).get(file_name, {})
        self.types: list[str] = []
        self.actions: list[str] = []
        for i, header in enumerate(headers):
            ctype = classify_header(header)
            if ctype == "category":
                col = [row[i] if i < len(row) else None for row in (all_rows or samples)]
                ctype = "numeric" if is_category(col) else None  # a short list of repeated labels is kept as is
                if ctype == "numeric":
                    ctx.kept.update(str(v).strip() for v in col if isinstance(v, str))
            if ctype is None:
                col = [row[i] if i < len(row) else None for row in samples]
                ctype = classify_by_content(col, ctx.scrubber.detector, float(tab["content_match_ratio"])) or "unknown"
            action = "keep" if ctype == "numeric" else rules.get(ctype, rules["unknown"])
            action = overrides.get(str(header), action)
            self.types.append(ctype)
            self.actions.append(action)
        self.unknown_action = rules["unknown"]

    def get(self, i: int) -> tuple[str, str]:
        if i < len(self.types):
            return self.types[i], self.actions[i]
        return "unknown", self.unknown_action

    @property
    def removed(self) -> set[int]:
        return {i for i, a in enumerate(self.actions) if a == "remove"}


class CellRewriter:
    def __init__(self, ctx: Context, plan: ColumnPlan, blank="", allow_remove: bool = True):
        self.ctx = ctx
        self.plan = plan
        self.blank = blank
        self.allow_remove = allow_remove  # XLSX blanks "remove" columns instead (keeps cell layout)

    def _token(self, etype: str, value) -> str:
        text = str(value)
        if isinstance(value, float) and value.is_integer():
            text = str(int(value))
        self.ctx.tally.counts[etype] += 1
        if etype == "EMAIL_ADDRESS" and EMAIL_IN_CELL.search(text):
            return EMAIL_IN_CELL.sub(lambda m: self.ctx.pseudo.token("EMAIL_ADDRESS", m.group(0)), text)
        return self.ctx.pseudo.token(etype, text)

    def _generalize(self, ctype: str, value):
        gen = self.ctx.gen
        tally = self.ctx.tally
        if ctype == "zip":
            tally.counts["ZIP_CODE"] += 1
            v = str(int(value)) if isinstance(value, (int, float)) else str(value)
            return gen.zip_text(v)
        if ctype in ("dob", "date"):
            out = gen.date_value(value, birth=(ctype == "dob"))
            if isinstance(value, (dt.date, dt.datetime)) or gen.has_date_element(str(value)):
                tally.counts["DATE_BIRTH" if ctype == "dob" else "DATE"] += 1
            y = value.year if isinstance(value, (dt.date, dt.datetime)) else gen.year_of(str(value))
            if y:
                tally.years.add(y)
            return out
        if ctype == "age":
            out = gen.age(value)
            if out != value:
                tally.counts["AGE_OVER_89"] += 1
            return out
        return None

    def rewrite(self, rows: list[list]) -> list[list]:
        """Rewrite a batch of rows. Free-text cells are scrubbed together (spaCy batching)."""
        out = [list(r) for r in rows]
        pending: list[tuple[int, int]] = []
        texts: list[str] = []
        for r, row in enumerate(out):
            for i, value in enumerate(row):
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                ctype, action = self.plan.get(i)
                if action == "keep" or (ctype in ("state", "country") and action != "drop"):
                    continue
                if action in ("drop", "remove"):
                    row[i] = self.blank
                    if ctype in COLUMN_ENTITY:
                        self.ctx.tally.counts[COLUMN_ENTITY[ctype]] += 1
                    continue
                if isinstance(value, bool):
                    continue
                if action == "generalize":
                    g = self._generalize(ctype, value)
                    if g is not None:
                        row[i] = g
                        continue
                    action = "pseudonymize"
                if action == "pseudonymize" and ctype in COLUMN_ENTITY and COLUMN_ENTITY[ctype] in TOKENIZED:
                    row[i] = self._token(COLUMN_ENTITY[ctype], value)
                    continue
                if isinstance(value, (int, float)) and abs(value) < 1_000_000:
                    continue  # small numbers in free-text/unknown columns are not identifiers
                if isinstance(value, (dt.date, dt.datetime, dt.time)):
                    if isinstance(value, dt.time):
                        continue
                    row[i] = self._generalize("date", value)
                    continue
                pending.append((r, i))
                texts.append(str(value) if not isinstance(value, float) or not value.is_integer() else str(int(value)))
        if texts:
            for (r, i), res in zip(pending, self.ctx.scrubber.scrub_many(texts, self.ctx.tally)):
                if res.findings:
                    out[r][i] = res.text
        if self.plan.removed and self.allow_remove:
            out = [[v for i, v in enumerate(row) if i not in self.plan.removed] for row in out]
        return out


TOKENIZED = {"PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "FAX_NUMBER", "US_SSN", "MEDICAL_RECORD_NUMBER",
             "HEALTH_PLAN_ID", "ACCOUNT_NUMBER", "LICENSE_NUMBER", "VEHICLE_ID", "DEVICE_ID", "URL",
             "IP_ADDRESS", "BIOMETRIC_ID", "OTHER_ID", "ORGANIZATION", "STREET_ADDRESS", "LOCATION"}


def scrub_header_row(headers: list, ctx: Context) -> list:
    """Headers are kept unless they are not ordinary column names and contain an identifier."""
    out = []
    for h in headers:
        if isinstance(h, str) and h.strip() and classify_header(h) is None:
            res = ctx.scrubber.scrub(h, ctx.tally)
            out.append(res.text)
        else:
            out.append(h)
    return out


_PUNCT = re.compile(r"[\s.,;:/()\[\]\-+$%#|*'\"]")


def needs_check(value: str) -> bool:
    """Does a scrubbed cell need the full re-detection? Cells holding only our tokens,
    punctuation or a short number (year, amount, count) have nothing left to find;
    anything with letters or a longer number (a phone, an ID) is checked."""
    from ..detect.engine import ARTIFACT_RE
    rest = _PUNCT.sub("", ARTIFACT_RE.sub(" ", value))
    return bool(rest) and not re.fullmatch(r"\d{1,6}", rest)


def _residual_rows(rows: list[list], ctx: Context, where: str, buf: list[str]) -> None:
    for row in rows:
        buf.extend(str(v) for v in row if v not in (None, "") and str(v).strip() not in ctx.kept and needs_check(str(v)))
    if sum(len(x) for x in buf) >= RESIDUAL_CHARS:
        flush_residual(buf, ctx, where)


def flush_residual(buf: list[str], ctx: Context, where: str) -> None:
    if buf:
        ctx.residual_many(buf, where)
        buf.clear()


# ---------------------------------------------------------------- CSV

class CsvFormat:
    def __init__(self, encoding: str, delimiter: str, quotechar: str, newline: str):
        self.encoding = encoding
        self.delimiter = delimiter
        self.quotechar = quotechar
        self.newline = newline

    def reader(self, text_f):
        return csv.reader(text_f, delimiter=self.delimiter, quotechar=self.quotechar, doublequote=True)

    def write_rows(self, rows: list[list]) -> bytes:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=self.delimiter, quotechar=self.quotechar, doublequote=True,
                       lineterminator=self.newline, quoting=csv.QUOTE_MINIMAL)
        w.writerows(rows)
        enc = "utf-8" if self.encoding == "utf-8-sig" else self.encoding
        return buf.getvalue().encode(enc, errors="replace")


def sniff_csv(f: BinaryIO, tsv: bool) -> CsvFormat:
    f.seek(0)
    sample = f.read(1 << 20)
    f.seek(0)
    if sample.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        try:
            # ignore a multi-byte character cut at the sample boundary
            sample[:-4].decode("utf-8") if len(sample) == 1 << 20 else sample.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "cp1252"
    text = sample.decode(encoding, errors="replace")
    newline = "\r\n" if "\r\n" in text[:65536] else ("\r" if "\r" in text[:65536] and "\n" not in text[:65536] else "\n")
    delimiter, quotechar = ("\t" if tsv else ","), '"'
    if not tsv:
        try:
            d = csv.Sniffer().sniff(text[:65536], delimiters=",\t;|")
            delimiter, quotechar = d.delimiter, d.quotechar or '"'
        except csv.Error:
            pass
    return CsvFormat(encoding, delimiter, quotechar, newline)


def _text(f: BinaryIO, fmt: CsvFormat) -> io.TextIOWrapper:
    f.seek(0)
    return io.TextIOWrapper(f, encoding=fmt.encoding, errors="replace", newline="")


def scrub_csv_stream(src: BinaryIO, dst: BinaryIO, ctx: Context, item: str, tsv: bool = False,
                     resume: tuple[int, int] | None = None,
                     checkpoint: Callable[[int, int], None] | None = None) -> tuple[CsvFormat, int]:
    """Returns the detected CSV format and the number of columns removed."""
    fmt = sniff_csv(src, tsv)
    # pass 1: header + sample for column classification
    t = _text(src, fmt)
    reader = fmt.reader(t)
    headers = next(reader, None)
    if headers is None:
        t.detach()
        return fmt, 0
    samples = []
    for row in reader:
        samples.append(row)
        if len(samples) >= int(ctx.cfg["tabular"]["sample_rows"]):
            break
    t.detach()
    plan = ColumnPlan(headers, samples, ctx, Path(item).name)
    rewriter = CellRewriter(ctx, plan)

    # pass 2: stream
    t = _text(src, fmt)
    reader = fmt.reader(t)
    next(reader)
    done = 0
    if resume and resume[0] > 0:
        done, offset = resume
        dst.seek(offset)
        dst.truncate()
        for _ in range(done):
            next(reader)
    else:
        dst.seek(0)
        dst.truncate()
        if fmt.encoding == "utf-8-sig":
            dst.write(b"\xef\xbb\xbf")
        out_headers = scrub_header_row(headers, ctx)
        if plan.removed:
            out_headers = [h for i, h in enumerate(out_headers) if i not in plan.removed]
        dst.write(fmt.write_rows([out_headers]))
    batch: list[list] = []
    for row in reader:
        batch.append(row)
        if len(batch) >= BATCH_ROWS:
            dst.write(fmt.write_rows(rewriter.rewrite(batch)))
            done += len(batch)
            ctx.tally.records += len(batch)
            batch = []
            if checkpoint:
                dst.flush()
                checkpoint(done, dst.tell())
    if batch:
        dst.write(fmt.write_rows(rewriter.rewrite(batch)))
        ctx.tally.records += len(batch)
        done += len(batch)
    t.detach()
    dst.flush()
    return fmt, len(plan.removed)


def verify_csv(src: BinaryIO, dst: BinaryIO, fmt: CsvFormat, ctx: Context, item: str, removed: int = 0) -> None:
    """Re-open the output: same row count, same field count per row, then residual detection."""
    s_t, d_t = _text(src, fmt), _text(dst, fmt)
    try:
        s_rows, d_rows = fmt.reader(s_t), fmt.reader(d_t)
        n = 0
        buf: list[str] = []
        batch: list[list] = []
        for s_row in s_rows:
            d_row = next(d_rows, None)
            if d_row is None:
                ctx.issue(f"{item}: output has fewer rows than input")
                return
            if len(d_row) != len(s_row) - removed and any(v.strip() for v in s_row):
                ctx.issue(f"{item}: row {n + 1} has {len(d_row)} fields, input had {len(s_row)}")
                return
            batch.append(d_row)
            n += 1
            if len(batch) >= BATCH_ROWS:
                _residual_rows(batch, ctx, item, buf)
                batch = []
        if next(d_rows, None) is not None:
            ctx.issue(f"{item}: output has more rows than input")
        _residual_rows(batch, ctx, item, buf)
        flush_residual(buf, ctx, item)
    except csv.Error as exc:
        ctx.issue(f"{item}: scrubbed CSV does not parse ({exc})")
    finally:
        s_t.detach()
        d_t.detach()


def process_csv(src: Path, dst: Path, ctx: Context, resume=None, checkpoint=None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tsv = src.suffix.lower() == ".tsv"
    mode = "r+b" if (resume and resume[0] > 0 and dst.exists()) else "w+b"
    if mode == "w+b":
        resume = None
    with open(src, "rb") as s, open(dst, mode) as d:
        fmt, removed = scrub_csv_stream(s, d, ctx, ctx.out_rel, tsv, resume, checkpoint)
        verify_csv(s, d, fmt, ctx, ctx.out_rel, removed)


def scrub_csv_bytes(data: bytes, ctx: Context, item: str, tsv: bool = False) -> bytes:
    src, dst = io.BytesIO(data), io.BytesIO()
    fmt, removed = scrub_csv_stream(src, dst, ctx, item, tsv)
    verify_csv(src, dst, fmt, ctx, item, removed)
    return dst.getvalue()


# ---------------------------------------------------------------- XLSX

def _header_row(ws) -> int | None:
    for r, row in enumerate(ws.iter_rows(min_row=1, max_row=min(ws.max_row, 20), values_only=True), start=1):
        if sum(1 for v in row if isinstance(v, str) and v.strip()) >= 2:
            return r
    return None


def _scrub_formula(formula: str, ctx: Context) -> str:
    return re.sub(r'"([^"]*)"', lambda m: '"' + ctx.scrubber.scrub(m.group(1), ctx.tally).text + '"', formula)


def _safe_sheet_title(title: str, taken: set[str]) -> str:
    t = re.sub(r"[\[\]:*?/\\]", "_", title)[:31] or "Sheet"
    base, n = t, 2
    while t in taken:
        suffix = f" ({n})"
        t = base[: 31 - len(suffix)] + suffix
        n += 1
    return t


def scrub_workbook(wb, ctx: Context, item: str) -> None:
    from openpyxl.packaging.custom import CustomPropertyList

    p = wb.properties
    for attr in ("creator", "lastModifiedBy"):
        if getattr(p, attr, None):
            setattr(p, attr, "Data Frenzy Scrubber")
            ctx.tally.counts["PERSON"] += 1
    for attr in ("title", "subject", "description", "keywords", "category", "identifier", "contentStatus"):
        v = getattr(p, attr, None)
        if v:
            setattr(p, attr, ctx.scrubber.scrub(str(v), ctx.tally).text)
    if p.created:
        p.created = dt.datetime(p.created.year, 1, 1)
    p.modified = dt.datetime(p.modified.year, 1, 1) if p.modified else None
    p.lastPrinted = None
    wb.custom_doc_props = CustomPropertyList()

    taken: set[str] = set()
    for ws in wb.worksheets:
        # pivot caches hold copies of the original data; images may show faces (Phase 3)
        if getattr(ws, "_pivots", None):
            ctx.quarantine(f"{item}!{ws.title} pivot tables", "pivot cache holds unscrubbed copies of data")
            ws._pivots = []
        if getattr(ws, "_images", None):
            ctx.quarantine(f"{item}!{ws.title} images", "pictures in spreadsheets are removed (the spreadsheet "
                                                           "library can't put a scrubbed copy back)")
            ws._images = []
        new_title = ctx.scrubber.scrub(ws.title, ctx.tally).text
        if new_title != ws.title:
            ws.title = _safe_sheet_title(new_title, taken)
        taken.add(ws.title)
        for hf in (ws.oddHeader, ws.oddFooter, ws.evenHeader, ws.evenFooter, ws.firstHeader, ws.firstFooter):
            for part in (hf.left, hf.center, hf.right):
                if part.text:
                    part.text = ctx.scrubber.scrub(part.text, ctx.tally).text
        _scrub_sheet(ws, ctx, item)


def _scrub_sheet(ws, ctx: Context, item: str) -> None:
    hdr = _header_row(ws)
    rows = list(ws.iter_rows())
    if not rows:
        return
    if hdr is None:
        headers: list = []
        data_start = 0
    else:
        headers = [c.value for c in rows[hdr - 1]]
        data_start = hdr
        # anything above the header row (titles, report headers) is free text
        for row in rows[: hdr - 1]:
            _scrub_free_cells(row, ctx)
        for cell, value in zip(rows[hdr - 1], scrub_header_row(headers, ctx)):
            if cell.data_type != "f":
                cell.value = value
    sample_n = int(ctx.cfg["tabular"]["sample_rows"])
    samples = [[c.value for c in row] for row in rows[data_start: data_start + sample_n]]
    plan = ColumnPlan(headers, samples, ctx, f"{Path(item).name}!{ws.title}",
                      all_rows=[[c.value for c in row] for row in rows[data_start:]])
    rewriter = CellRewriter(ctx, plan, blank=None, allow_remove=False)
    tidy = hdr is not None and ctx.cfg["tabular"].get("tidy", True)
    originals = {j: [row[j].value if j < len(row) else None for row in rows[data_start:]]
                 for j, t in enumerate(plan.types) if t == "date"} if tidy else {}
    for b in range(data_start, len(rows), BATCH_ROWS):
        chunk = rows[b: b + BATCH_ROWS]
        values = [[None if c.data_type == "f" else c.value for c in row] for row in chunk]
        for row, new in zip(chunk, rewriter.rewrite(values)):
            for cell, v in zip(row, new):
                if cell.data_type == "f":
                    cell.value = _scrub_formula(str(cell.value), ctx)
                elif v is not cell.value and v != cell.value:
                    if isinstance(v, int) and isinstance(cell.value, (dt.date, dt.datetime)):
                        cell.number_format = "0"
                    cell.value = v
        ctx.tally.records += len(chunk)
    if tidy:
        from .tidy import tidy_sheet
        ctx.kept |= tidy_sheet(ws, hdr, [c.value for c in rows[hdr - 1]], plan.types, originals)
    for row in rows:
        for cell in row:
            if cell.comment is not None:
                from openpyxl.comments import Comment
                text = ctx.scrubber.scrub(cell.comment.text or "", ctx.tally).text
                author = ctx.pseudo.token("PERSON", cell.comment.author) if cell.comment.author else None
                cell.comment = Comment(text, author or "Data Frenzy Scrubber")
            if cell.hyperlink is not None and (cell.hyperlink.target or "").strip():
                ctx.tally.counts["URL"] += 1
                cell.hyperlink = None


def _scrub_free_cells(row, ctx: Context) -> None:
    for cell in row:
        if isinstance(cell.value, str) and cell.value.strip() and cell.data_type != "f":
            cell.value = ctx.scrubber.scrub(cell.value, ctx.tally).text


def verify_workbook(data: bytes, expected: list[tuple[str, int, int]], ctx: Context, item: str) -> None:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data))
    except Exception as exc:
        ctx.issue(f"{item}: scrubbed workbook does not open ({type(exc).__name__})")
        return
    got = [(ws.title, ws.max_row, ws.max_column) for ws in wb.worksheets]
    if got != expected:
        ctx.issue(f"{item}: workbook structure changed during scrubbing")
    buf: list[str] = []
    for ws in wb.worksheets:
        where = f"{item}!{ws.title}"
        batch = []
        for row in ws.iter_rows(values_only=True):
            batch.append([v for v in row if isinstance(v, str)])
            if len(batch) >= BATCH_ROWS:
                _residual_rows(batch, ctx, where, buf)
                batch = []
        _residual_rows(batch, ctx, where, buf)
        for row in ws.iter_rows():
            for cell in row:
                if cell.comment is not None:
                    buf.append(cell.comment.text or "")
        flush_residual(buf, ctx, where)


def scrub_xlsx_bytes(data: bytes, ctx: Context, item: str) -> bytes:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), keep_vba=False, keep_links=False, rich_text=False)
    scrub_workbook(wb, ctx, item)
    expected = [(ws.title, ws.max_row, ws.max_column) for ws in wb.worksheets]
    out = io.BytesIO()
    wb.save(out)
    result = out.getvalue()
    verify_workbook(result, expected, ctx, item)
    return result


def process_xlsx(src: Path, dst: Path, ctx: Context) -> None:
    out = scrub_xlsx_bytes(src.read_bytes(), ctx, ctx.out_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    if dst.read_bytes() != out:
        ctx.issue(f"{ctx.out_rel}: output file differs from what was written")


def process_ods(src: Path, dst: Path, ctx: Context) -> None:
    """OpenDocument spreadsheet: read into a workbook, scrubbed and written as .xlsx."""
    from . import ods
    out = scrub_xlsx_bytes(ods.to_xlsx_bytes(src), ctx, ctx.out_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    if dst.read_bytes() != out:
        ctx.issue(f"{ctx.out_rel}: output file differs from what was written")
