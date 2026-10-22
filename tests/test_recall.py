"""Recall per identifier type on synthetic (Faker) free text.

For each type we generate N sentences from varied templates, scrub them, and
count an identifier as caught only if none of its identifying parts survive.
Results are written to /tmp/recall.csv and printed with -s.
"""

import csv
import re
import pytest

from fixtures.synth import Synth

N = 200
TARGET = 0.98  # names, emails, phones, dates, ID numbers
TARGET_OTHER = 0.95

T = {
    "name": ["Patient {name} was seen in clinic today.", "I spoke with {name} about the balance.",
             "{name} called to reschedule her appointment.", "Please forward the records to {name}.",
             "Emergency contact: {relative} (spouse).", "Her daughter {first} {last} will pick her up.",
             "cc: {name}", "Referred by Dr. {last} for evaluation.", "Hi {first},\nthe labs are back.",
             "Thanks,\n{first}", "Met with {name} and {relative} regarding discharge planning.",
             "{last}, {first} - no show"],
    "email": ["Send it to {email} please.", "Contact: {email}", "<{email}> wrote:", "email {email} with questions"],
    "phone": ["Call {phone} after 5pm.", "Phone: {phone}", "She can be reached at {phone}.", "cell {phone} (text ok)"],
    "date": ["Seen on {date} for follow-up.", "DOB: {dob}", "Admitted {date}, discharged {date2}.",
             "Next appointment {date}.", "Date of service: {date}", "Surgery scheduled for {date}."],
    "ssn": ["SSN {ssn}", "Social security number: {ssn}", "ssn on file {ssn}."],
    "mrn": ["MRN {mrn}", "Medical record number: {mrn}", "MRN# {mrn} - please merge charts", "Chart no. {mrn}"],
    "health_plan": ["Member ID: {member_id}", "Medicare MBI {mbi}", "Medicaid ID {medicaid}",
                    "Insurance ID {member_id} verified", "Policy number {member_id}"],
    "account": ["Account # {account}", "acct {account} past due", "Account number: {account}"],
    "license": ["NPI {npi}", "DEA {dea}", "State license # {state_license}", "License: {state_license}"],
    "other_id": ["Claim # {claim} denied.", "Case {case} escalated.", "Portal username: {username}",
                 "Follow {handle} for updates.", "Ticket #{claim}"],
    "address": ["Lives at {street}.", "Address: {street}, {city}, {state} {zip}", "Mail to {street}"],
    "city": ["Moved to {city}, {state} last spring.", "{city}, {state} {zip}"],
    "zip": ["{city}, {state} {zip}", "ZIP code {zip}"],
    "url": ["See {url} for results.", "Profile: {url}"],
    "ip": ["Login from {ip} flagged.", "IP address {ipv6}"],
    "vehicle": ["VIN {vin}", "License plate {plate} in the lot", "Vehicle VIN: {vin}"],
    "device": ["Device serial {device_serial}", "Implant S/N {device_serial}", "MAC {mac}"],
    "biometric": ["Fingerprint ID: {fingerprint}", "Voiceprint # {voiceprint}"],
    "age90": ["A {old_age}-year-old patient presented with dizziness.", "Age: {old_age}", "{old_age} y/o female"],
}
STRICT = {"name", "email", "phone", "date", "ssn", "mrn", "health_plan", "account", "license", "other_id"}


def _check(kind, template, p, synth):
    d1, d2, dob = synth.date_text(p.visit), synth.date_text(p.fake_date2), synth.date_text(p.dob)
    fields = dict(name=p.name, first=p.first, last=p.last, relative=p.relative, email=p.email, phone=p.phone,
                  date=d1, date2=d2, dob=dob, ssn=p.ssn, mrn=p.mrn, member_id=p.member_id, mbi=p.mbi,
                  medicaid=p.medicaid, account=p.account, npi=p.npi, dea=p.dea, state_license=p.state_license,
                  claim=p.claim, case=p.case, username=p.username, handle=p.handle, street=p.street, city=p.city,
                  state=p.state, zip=p.zip, url=p.url, ip=p.ip, ipv6=p.ipv6, vin=p.vin, plate=p.plate,
                  device_serial=p.device_serial, mac=p.mac, fingerprint=p.fingerprint, voiceprint=p.voiceprint,
                  old_age=p.old_age)
    text = template.format(**fields)
    used = [k for k in fields if "{" + k + "}" in template]
    return text, used, fields


def _leaked(kind, out, used, fields, p):
    digits_out = re.sub(r"\D", "", out)
    leaks = []
    for k in used:
        v = str(fields[k])
        if k in ("state",):
            continue
        if k in ("name", "first", "last", "relative"):
            for w in v.split():
                if len(w) > 1 and re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", out):
                    leaks.append(w)
        elif k in ("date", "date2", "dob"):
            if v in out:
                leaks.append(v)
            # month or day element surviving next to the year
            if re.search(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d", out):
                leaks.append("month")
        elif k == "phone":
            if re.sub(r"\D", "", v)[-7:] in digits_out:
                leaks.append(v)
        elif k == "street":
            num, word = v.split()[0], v.split()[1]
            if f"{num} {word}" in out:
                leaks.append(v)
        elif k == "zip":
            if v in out:
                leaks.append(v)
        elif k == "old_age":
            if re.search(rf"(?<!\d){v}(?![\d+])", out):  # "90+" is the generalized value
                leaks.append(v)
        elif k == "handle":
            if v.lstrip("@") in out:
                leaks.append(v)
        elif v.lower() in out.lower():
            leaks.append(v)
    return leaks


@pytest.fixture(scope="module")
def recall_results(scrubber):
    synth = Synth(seed=98765)
    results = {}
    for kind, templates in T.items():
        caught, total, misses = 0, 0, []
        for i in range(N):
            p = synth.person()
            p.fake_date2 = synth.fake.date_between(p.visit, "+30d") if hasattr(p, "visit") else p.visit
            text, used, fields = _check(kind, templates[i % len(templates)], p, synth)
            out = scrubber.scrub(text).text
            leaks = _leaked(kind, out, used, fields, p)
            total += 1
            if leaks:
                misses.append((text, out, leaks))
            else:
                caught += 1
        results[kind] = (caught / total, total, misses)
    rows = [(k, f"{r[0]:.4f}", r[1]) for k, r in results.items()]
    with open("/tmp/recall.csv", "w", newline="") as fh:
        csv.writer(fh).writerows([("identifier", "recall", "samples"), *rows])
    print("\nRecall on synthetic free text (N=%d per type):" % N)
    for k, rate, n in rows:
        print(f"  {k:12s} {rate}")
    return results


@pytest.mark.parametrize("kind", list(T))
def test_recall(recall_results, kind):
    rate, total, misses = recall_results[kind]
    target = TARGET if kind in STRICT else TARGET_OTHER
    sample = "\n".join(f"  IN : {m[0]!r}\n  OUT: {m[1]!r}\n  LEAK: {m[2]}" for m in misses[:5])
    assert rate >= target, f"{kind}: recall {rate:.3f} < {target}\n{sample}"
