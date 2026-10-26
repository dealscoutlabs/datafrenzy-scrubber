"""EHR export shapes found by running Synthea's public sample data: C-CDA XML, CSV tables."""

import csv
import io

from scrubber.formats.tabular import needs_check, scrub_csv_bytes
from scrubber.formats.xml_ import scrub_xml_bytes


def _ccda(p, doctor):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:sdtc="urn:hl7-org:sdtc">
  <title>Patient Record: {p.first}{57} {p.last}</title>
  <recordTarget><patientRole>
    <id root="2.16.840.1.113883.19.5" extension="034ccd79-62c4-ae8c-fe96-9f67dd6d7207"/>
    <id root="2.16.840.1.113883.4.1" extension="{p.ssn}"/>
    <addr use="HP"><streetAddressLine>{p.street}</streetAddressLine><city>Dedham</city>
      <state>Massachusetts</state><postalCode>02026</postalCode></addr>
    <telecom value="tel:{p.phone}"/>
    <patient><name><given>{p.first}{57}</given><family>{p.last}</family></name>
      <birthTime value="19660704135757"/>
      <raceCode code="2106-3" displayName="white" codeSystem="2.16.840.1.113883.6.238"/></patient>
  </patientRole></recordTarget>
  <author><assignedAuthor><assignedPerson><name><prefix>Dr.</prefix><given>{doctor.first}</given>
    <family>{doctor.last}</family></name></assignedPerson>
    <representedOrganization><name>LAKESHORE MEDICINE PHYSICIANS OF THE NORTH SHORE</name></representedOrganization>
  </assignedAuthor></author>
  <component><structuredBody><component><section>
    <title>Allergies</title>
    <text><table><tbody><tr><td>2019</td><td>Hypertension (disorder)</td><td>SNOMED-CT 38341003</td>
      <td>Seen by Dr. {doctor.last} for {p.first}{57}</td></tr></tbody></table></text>
    <entry><observation classCode="OBS" moodCode="EVN"><!-- Result observation -->
      <code code="8302-2" codeSystem="2.16.840.1.113883.6.1" displayName="Body Height"/>
      <effectiveTime value="20190402013621"/><value unit="cm" value="172.5"/></observation></entry>
  </section></component></structuredBody></component>
</ClinicalDocument>
"""


def test_ccda_document(synth, ctx):
    p, doctor = synth.person(), synth.person()
    out = scrub_xml_bytes(_ccda(p, doctor).encode(), ctx, "record.xml").decode()
    for secret in (p.last, f"{p.first}{57}", p.ssn, p.phone, p.street, "Dedham", "02026", doctor.last,
                   "19660704", "20190402", "LAKESHORE", "034ccd79-62c4"):
        assert secret not in out, secret
    for kept in ('displayName="Body Height"', "Hypertension (disorder)", "SNOMED-CT 38341003",
                 "<state>Massachusetts</state>", 'value="172.5"', "<prefix>Dr.</prefix>",
                 'root="2.16.840.1.113883.19.5"', "<title>Allergies</title>", "<!-- Result observation -->"):
        assert kept in out, kept
    assert '<birthTime value="1966"' in out and "020" in out
    assert not ctx.tally.issues, ctx.tally.issues


def _rows(data: bytes):
    return list(csv.DictReader(io.StringIO(data.decode())))


def test_ehr_csv_columns(synth, ctx):
    p = synth.person()
    src = ("Id,BIRTHDATE,BIRTHPLACE,FIRST,LAST,MAIDEN,ADDRESS,LAT,LON,FIPS,MEMBERID,PATIENTID\n"
           f"034ccd79-62c4-ae8c-fe96-9f67dd6d7207,1966-07-04,Dedham Massachusetts US,{p.first},{p.last},"
           f"{p.relative.split()[-1]},{p.street},42.2432,-71.1662,25021,{p.member_id},034ccd79-62c4-ae8c-fe96-9f67dd6d7207\n")
    out = _rows(scrub_csv_bytes(src.encode(), ctx, "patients.csv"))[0]
    assert out["LAT"] == "" and out["LON"] == "" and out["FIPS"] == ""
    assert "Dedham" not in out["BIRTHPLACE"] and out["BIRTHDATE"] == "1966"
    for k in ("FIRST", "LAST", "MAIDEN", "ADDRESS", "MEMBERID", "PATIENTID", "Id"):
        assert out[k] and out[k] not in src, k


def test_question_answer_rows_and_terminology(synth, ctx):
    p = synth.person()
    src = ("DATE,PATIENT,CODE,DESCRIPTION,VALUE,TYPE\n"
           "2019-04-02,x1,56799-0,Address,550 Hand Parade Unit 88,text\n"
           f"2019-04-02,x1,56799-1,Phone number,{p.phone},text\n"
           "2019-04-02,x1,8302-2,Body Height,172.5,numeric\n"
           "2019-04-02,x1,21908-9,Stage group.clinical Cancer,T1 category (qualifier value),text\n"
           "2019-04-02,x1,314231,NDA020503 200 ACTUAT Albuterol 0.09 MG/ACTUAT Metered Dose Inhaler,1,numeric\n")
    rows = _rows(scrub_csv_bytes(src.encode(), ctx, "observations.csv"))
    assert "Hand Parade" not in rows[0]["VALUE"] and p.phone not in rows[1]["VALUE"]
    assert rows[2]["VALUE"] == "172.5" and rows[2]["DESCRIPTION"] == "Body Height"
    assert rows[3]["VALUE"] == "T1 category (qualifier value)"
    assert rows[4]["DESCRIPTION"].startswith("NDA020503 200 ACTUAT Albuterol")
    assert not ctx.tally.issues, ctx.tally.issues


def test_drug_names_are_not_people(ctx):
    for text in ("24 HR metoprolol succinate 100 MG Extended Release Oral Tablet", "Afluria PF 30 mcg/0.3 mL dose"):
        assert ctx.scrubber.scrub(text).text == text
    assert "Lopez" not in ctx.scrubber.scrub("Maria Lopez 30 mg daily").text


def test_decimals_are_not_ids():
    assert not needs_check("0.42836487523263383") and needs_check("528469349999995")


def test_clinical_tables_are_not_damaged(ctx):
    """Shaped like MIMIC-IV (already de-identified): drugs, units, codes, doses and categories stay."""
    src = ("subject_id,hadm_id,drug,dose_val_rx,dose_unit_rx,route,ndc,icd_code,long_title,valueuom,"
           "statusdescription,dose_due,admission_location,careunit\n")
    rows = [("Fentanyl Citrate", "12.5-25", "mcg", "IV", "00409909422", "Y92230", "Reiter's disease", "K/uL",
             "FinishedRunning", "1-2", "TRANSFER FROM HOSPITAL", "Med/Surg"),
            ("Metoprolol Tartrate", "25", "mg", "PO", "00002821501", "G40909", "Parkinson's disease", "mg/dL",
             "Rewritten", "1.2", "EMERGENCY ROOM", "Neuro Surgical Intensive Care Unit (Neuro SICU)"),
            ("Insulin - Humalog", "0", "UNIT", "SUBCUT", "00002751001", "Z87891", "Percutaneous balloon valvuloplasty",
             "mIU/mL", "Paused", "0", "SKILLED NURSING FACILITY", "Med/Surg")] * 4
    src += "".join(f"{10000032 + i},{22595853 + i}," + ",".join(r) + "\n" for i, r in enumerate(rows))
    out = _rows(scrub_csv_bytes(src.encode(), ctx, "prescriptions.csv"))
    for row, original in zip(out, rows):
        assert tuple(row[k] for k in ("drug", "dose_val_rx", "dose_unit_rx", "route", "ndc", "icd_code", "long_title",
                                      "valueuom", "statusdescription", "dose_due", "admission_location",
                                      "careunit")) == original
        assert row["subject_id"] != "10000032"           # the IDs themselves are still replaced
