"""FHIR bundles (EHR exports): structure-aware scrubbing, codes kept, links preserved."""

import base64
import json

from scrubber.formats.json_ import scrub_json_bytes


def _bundle(p, doctor):
    note = f"\n2019-04-02\n\n# History\n{p.first}{482} is a 12 year-old. Seen by Dr. {doctor.last}. Call {p.phone}.\n"
    pid = "f75ba0c0-3571-3d63-e40b-fbd8571ce025"
    return {
        "resourceType": "Bundle", "type": "transaction",
        "entry": [
            {"fullUrl": f"urn:uuid:{pid}", "resource": {
                "resourceType": "Patient", "id": pid,
                "text": {"status": "generated", "div": f"<div>{p.name}</div>"},
                "extension": [
                    {"url": "http://hl7.org/fhir/StructureDefinition/patient-mothersMaidenName",
                     "valueString": p.relative},
                    {"url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race", "extension": [
                        {"url": "text", "valueString": "White"}]}],
                "identifier": [{"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                                                     "code": "SS", "display": "Social Security Number"}],
                                         "text": "Social Security Number"},
                                "system": "http://hl7.org/fhir/sid/us-ssn", "value": p.ssn},
                               {"system": "http://hospital.example.org", "value": p.mrn}],
                "name": [{"use": "official", "family": p.last, "given": [f"{p.first}{482}"], "prefix": ["Mr."]}],
                "telecom": [{"system": "phone", "value": p.phone, "use": "home"},
                            {"system": "email", "value": p.email}],
                "gender": "male", "birthDate": "1966-03-10",
                "address": [{"extension": [{"url": "http://hl7.org/fhir/StructureDefinition/geolocation",
                                            "extension": [{"url": "latitude", "valueDecimal": 42.2432},
                                                          {"url": "longitude", "valueDecimal": -72.5868}]}],
                             "line": [p.street], "city": "Holyoke", "state": "MA", "postalCode": "01040",
                             "country": "US"}]}},
            {"fullUrl": "urn:uuid:aaaaaaaa-1111-2222-3333-444444444444", "resource": {
                "resourceType": "Encounter", "id": "aaaaaaaa-1111-2222-3333-444444444444", "status": "finished",
                "type": [{"coding": [{"system": "http://snomed.info/sct", "code": "410620009",
                                      "display": "Well child visit (procedure)"}],
                          "text": "Well child visit (procedure)"}],
                "subject": {"reference": f"urn:uuid:{pid}", "display": f"Mr. {p.first}{482} {p.last}"},
                "participant": [{"individual": {
                    "reference": "Practitioner?identifier=http://hl7.org/fhir/sid/us-npi|9999981894",
                    "display": f"Dr. {doctor.first} {doctor.last}"}}],
                "period": {"start": "2019-04-02T01:36:21+00:00", "end": "2019-04-02T01:54:58+00:00"},
                "serviceProvider": {"reference": "Organization?identifier=https://example.org|b03b624d",
                                    "display": "LAKESHORE HEALTH CENTER INC"}}},
            {"fullUrl": "urn:uuid:bbbbbbbb-1111-2222-3333-444444444444", "resource": {
                "resourceType": "DocumentReference", "id": "bbbbbbbb-1111-2222-3333-444444444444",
                "subject": {"reference": f"urn:uuid:{pid}"},
                "content": [{"attachment": {"contentType": "text/plain; charset=utf-8",
                                            "data": base64.b64encode(note.encode()).decode()}}]}},
        ]}


def test_fhir_bundle(synth, ctx):
    p, doctor = synth.person(), synth.person()
    src = json.dumps(_bundle(p, doctor), indent=1).encode()
    out = scrub_json_bytes(src, ctx, "patient.json")
    text = out.decode()
    doc = json.loads(text)
    patient, enc, docref = (e["resource"] for e in doc["entry"])
    note = base64.b64decode(docref["content"][0]["attachment"]["data"]).decode()
    hay = text + note
    for secret in (p.last, f"{p.first}{482}", p.ssn, p.mrn, p.phone, p.email, p.street, doctor.last,
                   p.relative.split()[-1], "Holyoke", "LAKESHORE", "9999981894", "1966-03-10", "42.2432",
                   "f75ba0c0-3571-3d63-e40b-fbd8571ce025"):
        assert secret not in hay, secret
    # clinical content and structure stay
    assert enc["type"][0]["coding"][0]["display"] == "Well child visit (procedure)"
    assert patient["gender"] == "male" and patient["birthDate"] == "1966" and patient["address"][0]["state"] == "MA"
    assert patient["address"][0]["postalCode"] == "010XX" or patient["address"][0]["postalCode"].startswith("010")
    assert patient["identifier"][0]["type"]["text"] == "Social Security Number"
    assert "us-core-race" in json.dumps(patient) and "White" in json.dumps(patient)
    # the encounter still points at the same (renamed) patient
    assert enc["subject"]["reference"] == f"urn:uuid:{patient['id']}"
    assert enc["subject"]["display"].startswith("Mr. PERSON_")
    assert enc["subject"]["display"].split()[-1] == patient["name"][0]["family"]
    assert "narrative removed" in patient["text"]["div"]
    assert note.lstrip().startswith("2019") and "PERSON_" in note


def test_label_never_takes_value_from_next_string(ctx):
    """Strings scrubbed together must not lend a label to the next one ("Member ID" | "1978-03-10")."""
    out = ctx.scrubber.scrub_joined(["Medicaid Member ID", "1978-03-10"], ctx.tally)
    assert out[1].text == "1978"


def test_fhir_file_named_after_patient(workspace, synth, run_pipeline):
    """EHR exports name each file after the patient and record id; accented names arrive decomposed on Macs."""
    import unicodedata
    p, doctor = synth.person(), synth.person()
    bundle = _bundle(p, doctor)
    bundle["entry"][0]["resource"]["name"][0]["given"] = ["Raúl937"]
    name = unicodedata.normalize("NFD", f"Raúl937_{p.last}_f75ba0c0-3571-3d63-e40b-fbd8571ce025.json")
    (workspace.input / name).write_text(json.dumps(bundle))
    runner, _ = run_pipeline(workspace)
    (row,) = [r for r in runner.store.files() if r["kind"] == "json"]
    out = unicodedata.normalize("NFC", row["out_rel"])
    assert "Raúl" not in out and "Rau" not in out and p.last not in out and "f75ba0c0" not in out, out
    assert out.endswith(".json")
