import json
import re

from scrubber.formats.json_ import scrub_json_bytes, shape


def test_key_rules_and_structure(ctx, synth):
    p = synth.person()
    doc = {"patients": [{"id": 1234567, "name": p.name, "first_name": p.first, "email": p.email, "phone": p.phone,
                         "dob": p.dob.isoformat(), "age": 93, "zip": p.zip, "state": p.state,
                         "mrn": int(p.mrn) if p.mrn.isdigit() else p.mrn, "notes": f"Spoke with {p.relative} at {p.phone}",
                         "created_at": 1647252000, "tags": ["vip", p.city], "active": True, "balance": 12.5,
                         "address": {"street": p.street, "city": p.city}}]}
    src = json.dumps(doc, indent=2).encode()
    out = json.loads(scrub_json_bytes(src, ctx, "patients.json"))
    assert shape(out) == shape(doc)
    rec = out["patients"][0]
    assert isinstance(rec["id"], int) and rec["id"] != 1234567
    assert re.fullmatch(r"EMAIL_\d{4}", rec["email"]) and re.fullmatch(r"PHONE_\d{4}", rec["phone"])
    assert rec["dob"] in ("90+",) or re.fullmatch(r"\d{4}", rec["dob"])
    assert rec["age"] == "90+" and re.fullmatch(r"\d{3}XX", rec["zip"]) and rec["state"] == p.state
    assert p.relative.split()[0] not in rec["notes"] and "PHONE_" in rec["notes"]
    assert rec["created_at"] == 1640995200  # 2022-01-01T00:00:00Z
    assert rec["active"] is True and rec["balance"] == 12.5 and rec["tags"][0] == "vip"
    assert re.fullmatch(r"ADDRESS_\d{4}", rec["address"]["street"])
    assert src.decode().startswith('{\n  "patients"')  # indentation detected and kept
    assert json.dumps(out, indent=2).startswith('{\n  "patients"')


def test_jsonl(ctx, synth):
    people = [synth.person() for _ in range(5)]
    src = "".join(json.dumps({"from": p.email, "body": f"Call {p.phone}"}) + "\n" for p in people).encode()
    out = scrub_json_bytes(src, ctx, "log.jsonl", jsonl=True).decode()
    lines = out.splitlines()
    assert len(lines) == 5 and all(json.loads(x) for x in lines)
    for p in people:
        assert p.email not in out and p.phone not in out
