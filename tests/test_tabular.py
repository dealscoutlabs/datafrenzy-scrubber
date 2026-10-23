import csv
import io
import re

from openpyxl import load_workbook

from scrubber.formats.columns import classify_header
from scrubber.formats.tabular import scrub_csv_bytes, scrub_xlsx_bytes


def test_header_classification():
    expect = {"Patient Name": "person_name", "DOB": "dob", "Date of Birth": "dob", "Email Address": "email",
              "IP Address": "ip", "Home Phone": "phone", "Fax": "fax", "SSN": "ssn", "MRN": "mrn",
              "Patient ID": "mrn", "Member ID": "health_plan", "Subscriber Name": "person_name",
              "Account Number": "account", "Account Name": "organization", "NPI": "license", "VIN": "vehicle",
              "Serial Number": "device", "Website": "url", "Street Address": "address", "City": "city",
              "County": "city", "State": "state", "ZIP": "zip", "Visit Date": "date", "Age": "age",
              "Employer": "organization", "Notes": "notes", "Claim Number": "other_id", "Username": "other_id",
              "Account Owner": "person_name", "Created By": "person_name", "Created Date": "date", "Copay": None,
              "Admission Id": "other_id", "Discharge Type": "category", "Discharge Date": "date",
              "Admission Date": "date", "Assigned Therapist": "person_name", "Date Signed": "date",
              "Current Service Facility Name": "organization", "Visit Status": "category"}
    got = {h: classify_header(h) for h in expect}
    assert got == expect


def _read(data: bytes):
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def test_csv_structure_and_rules(synth, ctx):
    people = [synth.person() for _ in range(25)]
    src = synth.csv_text(people).encode()
    out = scrub_csv_bytes(src, ctx, "patients.csv")
    a, b = _read(src), _read(out)
    assert len(a) == len(b) and all(len(x) == len(y) for x, y in zip(a, b))
    assert b[0] == a[0]  # headers preserved
    h = {name: i for i, name in enumerate(a[0])}
    text = out.decode()
    for p in people:
        for secret in (p.name, p.email, p.ssn, p.mrn, p.member_id, p.npi, p.street, p.relative):
            assert secret not in text, secret
    for orig, row in zip(a[1:], b[1:]):
        assert re.fullmatch(r"PERSON_\d{4}", row[h["Patient Name"]])
        assert re.fullmatch(r"(\d{4}|90\+)", row[h["DOB"]])
        assert re.fullmatch(r"\d{4}", row[h["Visit Date"]])
        assert re.fullmatch(r"\d{3}XX", row[h["ZIP"]])
        assert row[h["State"]] == orig[h["State"]]
        assert row[h["Copay"]] == orig[h["Copay"]] and row[h["Status"]] == orig[h["Status"]]
        assert row[h["Age"]] == ("90+" if int(orig[h["Age"]]) >= 90 else orig[h["Age"]])
        assert "PERSON_" in row[h["Notes"]] and "PHONE_" in row[h["Notes"]]
    # same person -> same token in "Patient Name" and in the free-text notes (relative shares last name, not token)
    assert ctx.tally.records == 25 and ctx.tally.issues == []


def test_csv_preserves_delimiter_bom_and_crlf(synth, ctx):
    people = [synth.person() for _ in range(3)]
    src = ("﻿" + synth.csv_text(people, delimiter=";")).encode("utf-8")
    out = scrub_csv_bytes(src, ctx, "semi.csv")
    assert out.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in out and out.count(b";") >= src.count(b";") - 10
    assert out.decode("utf-8-sig").splitlines()[0] == ";".join(synth.HEADERS)


def test_csv_quoted_newlines_kept(ctx):
    src = b'Name,Notes\r\n"Ignatius Vandermolen","line one\nCall (212) 555-0101"\r\n'
    out = scrub_csv_bytes(src, ctx, "q.csv")
    rows = _read(out)
    assert len(rows) == 2 and "\n" in rows[1][1] and "555-0101" not in rows[1][1]


def test_content_based_column_detection(ctx):
    src = "col_a,col_b\r\n" + "".join(f"v{i}@mailinator.example.com,{i}\r\n" for i in range(30))
    out = _read(scrub_csv_bytes(src.encode(), ctx, "anon.csv"))
    assert all(re.fullmatch(r"EMAIL_\d{4}", r[0]) for r in out[1:])
    assert [r[1] for r in out[1:]] == [str(i) for i in range(30)]


def test_xlsx_formatting_and_structure_preserved(synth, ctx, monkeypatch):
    monkeypatch.setitem(ctx.cfg["tabular"], "tidy", False)  # the original layout, cell for cell
    people = [synth.person() for _ in range(10)]
    src = synth.xlsx_bytes(people)
    out = scrub_xlsx_bytes(src, ctx, "patients.xlsx")
    a, b = load_workbook(io.BytesIO(src)), load_workbook(io.BytesIO(out))
    assert a.sheetnames == b.sheetnames
    for wa, wb_ in zip(a.worksheets, b.worksheets):
        assert (wa.max_row, wa.max_column) == (wb_.max_row, wb_.max_column)
    ws = b["Patients"]
    assert ws["A1"].font.bold and ws["A1"].fill.fgColor.rgb.endswith("1F4E78")
    assert ws.column_dimensions["A"].width == 28
    assert [c.value for c in ws[1]] == synth.HEADERS
    assert re.fullmatch(r"PERSON_\d{4}", ws["A2"].value)
    assert ws["D2"].value in ("90+",) or isinstance(ws["D2"].value, int)
    assert isinstance(ws["R2"].value, int)
    assert ws["A2"].hyperlink is None
    assert people[0].relative.split()[0] not in ws["T2"].comment.text
    assert people[0].phone not in ws["T2"].comment.text
    assert b["Summary"]["B3"].value == "=B2*2"
    assert people[0].name not in (b["Summary"]["A1"].value or "")
    assert b.properties.creator == "Data Frenzy Scrubber"
    assert people[0].employer not in (b.properties.title or "")
    flat = " ".join(str(c.value) for wsx in b.worksheets for row in wsx.iter_rows() for c in row if c.value)
    for p in people:
        for secret in (p.name, p.email, p.ssn, p.mrn, p.phone):
            assert secret not in flat
    assert ctx.tally.issues == []


def test_discharge_report_columns(synth, ctx):
    """Shaped like a real treatment-center discharge report: IDs, categories and therapists keep their meaning."""
    import datetime as dt

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Discharges"
    ws.append(["Admission Id", "Current Service Facility Name", "Discharge Date", "Admission Date", "Patient Name",
               "Assigned Therapist", "Discharge Type", "Totals"])
    people = [synth.person() for _ in range(12)]
    types = ["Successful Completion", "External Transfer", "Medical", "Against Treatment Advice (ATA)"]
    for i, p in enumerate(people):
        ws.append([645000 + i * 37, "Lakeshore Recovery Center of TX", dt.datetime(2026, 5, 1 + i),
                   dt.datetime(2026, 3, 1 + i), p.name, people[-1 - i].name if i % 3 else None, types[i % 4], 1])
    buf = io.BytesIO()
    wb.save(buf)
    out = load_workbook(io.BytesIO(scrub_xlsx_bytes(buf.getvalue(), ctx, "discharge.xlsx")))["Discharges"]
    assert [c.value for c in out[1]] == ["Patient ID", "Facility", "Admission Year", "Discharge Year",
                                         "Length of Stay (Days)", "Assigned Therapist", "Discharge Type", "Totals"]
    rows = list(out.iter_rows(min_row=2, values_only=True))
    assert sorted(r[4] for r in rows) == sorted((dt.datetime(2026, 5, 1 + i) - dt.datetime(2026, 3, 1 + i)).days
                                                for i in range(12))
    for pid, fac, adm, dis, los, ther, dtype, total in rows:
        assert re.fullmatch(r"Patient \d+", pid) and re.fullmatch(r"Facility [A-Z]+", fac) and adm == dis == 2026
        assert ther is None or re.fullmatch(r"Therapist \d+", ther), ther
        assert dtype in types and total == 1
    assert len({r[1] for r in rows}) == 1
    flat = " ".join(str(v) for r in rows for v in r)
    for p in people:
        assert p.last not in flat
    # the same patient keeps the same number in another spreadsheet
    wb2 = Workbook()
    ws2 = wb2.active
    ws2.append(["Patient Name", "Discharge Type"])
    ws2.append([people[3].name, "Medical"])
    buf2 = io.BytesIO()
    wb2.save(buf2)
    out2 = load_workbook(io.BytesIO(scrub_xlsx_bytes(buf2.getvalue(), ctx, "other.xlsx"))).active
    assert out2["A2"].value == f"Patient {int(ctx.pseudo.token('PERSON', people[3].name).split('_')[1])}"
    assert out2["A2"].value in {r[0] for r in rows}
