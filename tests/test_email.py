import email
import re
from email import policy

from bs4 import BeautifulSoup

from scrubber.formats.email_ import leaf_signature, parse, scrub_message_bytes

TOKEN = re.compile(r"PERSON_\d{4}")


def _scrub(msg, ctx):
    data = msg.as_bytes(policy=policy.default)
    out = scrub_message_bytes(data, ctx, "test.eml")
    return data, out, email.message_from_bytes(out, policy=policy.default)


def _all_text(m):
    return m.as_string()


def test_headers_and_bodies_scrubbed(synth, ctx):
    a, b = synth.person(), synth.person()
    msg = synth.eml(a, b)
    _, out, m = _scrub(msg, ctx)
    text = out.decode("utf-8", "replace")
    for secret in (a.first, a.last, b.first, b.last, a.email, b.email, b.ssn, b.mrn, b.member_id, a.ip):
        assert secret not in text, secret
    assert TOKEN.search(str(m["From"])) and m["From"].addresses[0].domain == "scrubbed.invalid"
    assert m["Received"] is None and m["X-Originating-IP"] is None
    assert re.fullmatch(r"\w{3}, 01 Jan 2022 00:00:00 \+0000", str(m["Date"]))
    assert ctx.tally.issues == []


def test_display_name_same_token_in_header_and_body(synth, ctx):
    a, b = synth.person(), synth.person()
    _, _, m = _scrub(synth.eml(a, b), ctx)
    to_name = m["To"].addresses[0].display_name
    body = m.get_body(("plain",)).get_content()
    # "Patient <full name>" in the note uses the same token as the To: display name
    assert f"Patient {to_name}" in body


def test_cc_last_first_display_name_maps_to_same_person(synth, ctx):
    a, b = synth.person(), synth.person()
    _, _, m = _scrub(synth.eml(a, b), ctx)
    assert m["From"].addresses[0].display_name == m["Cc"].addresses[0].display_name


def test_mime_structure_and_html_markup_preserved(synth, ctx):
    a, b = synth.person(), synth.person()
    msg = synth.eml(a, b, csv_attachment=True)
    before = leaf_signature(msg)
    _, _, m = _scrub(msg, ctx)
    assert leaf_signature(m) == before
    html = m.get_body(("html",)).get_content()
    soup = BeautifulSoup(html, "html.parser")
    assert [t.name for t in soup.find_all(True)] == [t.name for t in BeautifulSoup(
        msg.get_body(("html",)).get_content(), "html.parser").find_all(True)]
    assert b.url not in html and b.email not in html
    assert soup.find("a", href=re.compile(r"^mailto:email_\d{4}@scrubbed\.invalid$"))


def test_csv_attachment_scrubbed_recursively(synth, ctx):
    a, b = synth.person(), synth.person()
    _, _, m = _scrub(synth.eml(a, b, csv_attachment=True), ctx)
    att = next(m.iter_attachments())
    content = att.get_content()
    content = content.decode() if isinstance(content, bytes) else content
    assert b.ssn not in content and b.last not in content
    assert content.splitlines()[0].startswith("Patient Name,First Name")
    assert b.last not in att.get_filename()


def test_nested_message_scrubbed(synth, ctx):
    a, b, c = synth.person(), synth.person(), synth.person()
    inner = synth.eml(c, b, html=False)
    _, out, m = _scrub(synth.eml(a, b, nested=inner), ctx)
    assert c.email.encode() not in out and c.last.encode() not in out
    nested = [p for p in m.walk() if p.get_content_type() == "message/rfc822"]
    assert nested and nested[0].get_payload(0)["From"].addresses[0].domain == "scrubbed.invalid"


def test_audio_replaced_and_listed_images_kept(synth, ctx, store):
    a, b = synth.person(), synth.person()
    msg = synth.eml(a, b, audio_attachment=True)
    from fixtures.synth_images import save, text_image
    msg.add_attachment(save(text_image([f"Patient {b.name}"]), "PNG"), maintype="image", subtype="png",
                       filename=f"{b.first}_photo.png")
    _, out, m = _scrub(msg, ctx)
    assert b"RIFF" not in out
    names = [p.get_filename() for p in m.iter_attachments()]
    assert sum(n.endswith(".removed.txt") for n in names) == 1
    assert any(n.endswith(".png") and b.first not in n for n in names)
    reasons = [q["reason"] for q in store.quarantined() if q["parent"] == ctx.rel]
    assert any("voiceprint" in r for r in reasons)


def test_threading_preserved(synth, ctx):
    a, b = synth.person(), synth.person()
    first = synth.eml(a, b)
    reply = synth.eml(b, a, reply_to=first)
    _, _, m1 = _scrub(first, ctx)
    _, _, m2 = _scrub(reply, ctx)
    assert str(m2["In-Reply-To"]) == str(m1["Message-ID"])
    assert m1["Message-ID"].endswith("@scrubbed.invalid>")
    assert m2["Subject"].startswith("Re: ") and str(m2["Subject"])[4:] == str(m1["Subject"])


def test_output_reparses_without_defects(synth, ctx):
    a, b = synth.person(), synth.person()
    _, out, _ = _scrub(synth.eml(a, b, csv_attachment=True, image_attachment=True), ctx)
    m = parse(out)
    assert all(not p.defects for p in m.walk())
    for p in m.walk():
        if not p.is_multipart():
            assert p.get_payload(decode=True) is not None


def test_crlf_and_non_utf8_charset(ctx):
    raw = ("From: =?iso-8859-1?q?Jos=E9_Ca=F1izares?= <jose@example.com>\r\nTo: x@example.org\r\n"
           "Subject: Cita de Jos\xe9\r\nDate: Mon, 3 Mar 2021 10:00:00 -0500\r\nMIME-Version: 1.0\r\n"
           "Content-Type: text/plain; charset=iso-8859-1\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
           "Hola, soy Jos\xe9 Ca\xf1izares, mi tel\xe9fono es (305) 555-0188.\r\n").encode("latin-1")
    out = scrub_message_bytes(raw, ctx, "latin.eml")
    assert b"\r\n" in out
    m = parse(out)
    body = m.get_content()
    assert "555-0188" not in body and "Ca\xf1izares" not in body
    assert "Ca\xf1izares" not in str(m["From"])
