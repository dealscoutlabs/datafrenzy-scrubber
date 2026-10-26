"""HL7 v2 messages: fields replaced by their meaning, codes and results kept."""

from scrubber.formats.hl7 import looks_like_hl7, scrub_hl7_text


def _message(p, kin, doc):
    return "\r".join([
        "MSH|^~\\&|EPIC|LAKESHORE GENERAL|LAB|LAKESHORE GENERAL|20190402013621||ADT^A01|MSG00001|P|2.5",
        "EVN|A01|20190402013621",
        f"PID|1||{p.mrn}^^^LKS^MR||{p.last}^{p.first}^Q^^^^L|{kin.last}|19660704|F|||{p.street}^^Duluth^MN^55803^USA"
        f"||(218)555-0142|^NET^Internet^{p.email}||||ACC{p.mrn}|{p.ssn}|D1234567^MN^20300101",
        f"NK1|1|{kin.last}^{kin.first}^^^^^L|DAU^Daughter|{kin.street}^^Duluth^MN^55803|(218)555-0177",
        f"PV1|1|I|4WEST^412^B^LAKESHORE GENERAL||||1234^{doc.last}^{doc.first}^^^DR^MD|||MED|||||||||V{p.mrn}"
        "|||||||||||||||||||||||||20190402013621|20190406090000",
        "OBX|1|NM|8302-2^Body Height^LN||172.5|cm|150-200|N|||F|||20190402",
        "OBX|2|CE|72166-2^Tobacco smoking status^LN||266919005^Never smoker^SCT||||||F",
        f"OBX|3|TX|11506-3^Progress note^LN||Seen with daughter {kin.first} {kin.last}. Call {p.phone}.||||||F",
        f"NTE|1|L|{p.first} prefers morning visits",
        f"IN1|1|BCBS01|PAYER77|Blue Cross Blue Shield|PO Box 64560^^St Paul^MN^55164|||GRP4455||||||||"
        f"{p.last}^{p.first}|SEL|19660704|{p.street}^^Duluth^MN^55803|||||||||||||||||||{p.member_id}",
        f"GT1|1||{p.last}^{p.first}||{p.street}^^Duluth^MN^55803|(218)555-0142||19660704||||{p.ssn}",
    ])


def test_hl7_message(synth, ctx):
    p, kin, doc = synth.person(), synth.person(), synth.person()
    src = _message(p, kin, doc)
    assert looks_like_hl7(src.encode())
    out = scrub_hl7_text(src, ctx, "adt.hl7")
    for secret in (p.last, p.first, p.mrn, p.street, p.email, p.ssn, p.phone, p.member_id, kin.last, kin.first,
                   doc.last, "Duluth", "55803", "(218)555-0142", "19660704", "20190402013621", "D1234567",
                   "20300101", "LAKESHORE"):
        assert secret not in out, secret
    for kept in ("8302-2^Body Height^LN||172.5|cm|150-200|N", "266919005^Never smoker^SCT", "^MN^558XX",
                 "ADT^A01", "11506-3^Progress note^LN", "|2019|", "|1966|", "DAU^Daughter"):
        assert kept in out, kept
    a, b = src.split("\r"), out.split("\r")
    assert len(a) == len(b) and all(x.count("|") == y.count("|") for x, y in zip(a, b))
    assert not ctx.tally.issues, ctx.tally.issues


def test_hl7_standard_example_lab_result(ctx):
    """The HL7 normative example (ORU^R01, "EVERYWOMAN^EVE"), doctors in shifted fields."""
    src = "\r".join([
        "MSH|^~\\&|GHH LAB|ELAB-3|GHH OE|BLDG4|200202150930||ORU^R01|CNTRL-3456|P|2.4",
        "PID|||555-44-4444||EVERYWOMAN^EVE^E^^^^L|JONES|196203520|F|||153 FERNWOOD DR.^^STATESVILLE^OH^35292"
        "||(206)3345232|(206)752-121||||AC555444444||67-A4335^OH^20030520",
        "OBR|1|845439^GHH OE|1045813^GHH LAB|1554-5^GLUCOSE|||200202150730||||||||555-55-5555^PRIMARY^PATRICIA P"
        "^^^^MD^^LEVEL SEVEN HEALTHCARE, INC.|||||||||F||||||444-44-4444^HIPPOCRATES^HOWARD H^^^^MD",
        "OBX|1|SN|1554-5^GLUCOSE^POST 12H CFST:MCNC:PT:SER/PLAS:QN||^182|mg/dl|70_105|H|||F",
    ])
    out = scrub_hl7_text(src, ctx, "oru.hl7")
    for secret in ("EVERYWOMAN", "JONES", "FERNWOOD", "STATESVILLE", "3345232", "AC555444444", "67-A4335",
                   "PATRICIA", "HIPPOCRATES", "555-55-5555", "444-44-4444", "LEVEL SEVEN", "196203520", "20030520"):
        assert secret not in out, secret
    assert "1554-5^GLUCOSE^POST 12H CFST:MCNC:PT:SER/PLAS:QN||^182|mg/dl|70_105|H" in out


def test_hl7_misplaced_fields_still_scrubbed(synth, ctx):
    """Feeds put values one field off: an ID or timestamp outside the map is still caught."""
    p = synth.person()
    src = f"MSH|^~\\&|A|B|C|D|20190402||ADT^A01|1|P|2.5\rPV1|1|I||||||||||||||||||V{p.mrn}|20190402013621"
    out = scrub_hl7_text(src, ctx, "x.hl7")
    assert p.mrn not in out and "20190402013621" not in out and "|2019" in out


def test_hl7_saved_as_text_is_recognised(workspace, run_pipeline):
    (workspace.input / "feed.txt").write_text("MSH|^~\\&|A|B|C|D|20190402||ADT^A01|1|P|2.5\rPID|1||123456||DOE^JANE")
    runner, _ = run_pipeline(workspace)
    (row,) = runner.store.files()
    assert row["kind"] == "hl7"
