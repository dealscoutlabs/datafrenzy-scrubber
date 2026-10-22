"""Email (.eml) scrubbing in place: headers, every MIME part, attachments.

Output is re-serialized valid MIME with the same part structure. Attachments
in formats this phase can't scrub are replaced by a short text note and
listed in the report (never exported as-is).
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import quopri
import re
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from pathlib import Path

from bs4 import BeautifulSoup, Comment

from .base import Context

ADDRESS_HEADERS = {"from", "to", "cc", "bcc", "reply-to", "sender", "resent-from", "resent-to", "resent-cc"}
ID_HEADERS = {"message-id", "in-reply-to", "references", "content-id"}
MIME_HEADERS = {"mime-version", "content-type", "content-transfer-encoding", "content-disposition",
                "content-id", "content-language", "content-description"}
DATE_HEADERS = {"date"}
VERBATIM_HEADERS = {"mime-version", "content-type", "content-transfer-encoding", "content-disposition",
                    "content-language", "importance", "priority", "x-priority"}
MAX_DEPTH = 12

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
URL_ATTRS = {"href", "src", "action", "background", "cite", "poster", "data", "longdesc", "srcset"}
TEXT_ATTRS = {"alt", "title", "value", "placeholder", "label", "content", "summary", "aria-label"}


def detect_linesep(data: bytes) -> str:
    head = data[:65536]
    return "\r\n" if head.count(b"\r\n") > head.count(b"\n") / 2 else "\n"


def parse(data: bytes, linesep: str = "\n") -> EmailMessage:
    return BytesParser(policy=policy.default.clone(linesep=linesep)).parsebytes(data)


def serialize(msg: Message, linesep: str = "\n") -> bytes:
    return msg.as_bytes(policy=msg.policy.clone(linesep=linesep, max_line_length=78))


# ---------------------------------------------------------------- headers

def _looks_like_org(name: str) -> bool:
    return bool(re.search(r"(?i)\b(inc|llc|ltd|corp|team|support|no-?reply|notifications?|billing|info|admin|"
                          r"office|clinic|hospital|health|medical|group|services|department|dept|center|"
                          r"pharmacy|lab|labs|newsletter|alerts?|mailer|daemon)\b", name))


def scrub_display_name(name: str, ctx: Context) -> str:
    name = name.strip().strip('"').strip()
    if not name:
        return ""
    if "@" in name:
        return ctx.scrubber.scrub(name, ctx.tally).text
    if _looks_like_org(name):
        scrubbed = ctx.scrubber.scrub(name, ctx.tally).text
        if scrubbed == name:
            # nothing detected - an org/team name may still identify the sender's employer
            ctx.tally.counts["ORGANIZATION"] += 1
            return ctx.pseudo.token("ORGANIZATION", name)
        return scrubbed
    ctx.tally.counts["PERSON"] += 1
    return ctx.pseudo.token("PERSON", name)


def scrub_addresses(value: str, ctx: Context) -> str:
    pairs = email.utils.getaddresses([value])
    out = []
    for name, addr in pairs:
        if not name and not addr:
            continue
        new_addr = ""
        if addr:
            if "@" in addr:
                ctx.tally.counts["EMAIL_ADDRESS"] += 1
                new_addr = ctx.pseudo.email_address(addr)
            else:
                ctx.tally.counts["EMAIL_ADDRESS"] += 1
                new_addr = ctx.pseudo.email_address(addr + "@local")
        new_name = scrub_display_name(name, ctx) if name else ""
        out.append(email.utils.formataddr((new_name, new_addr)) if new_name else new_addr)
    return ", ".join(out) if out else "undisclosed-recipients:;"


def scrub_msgids(value: str, ctx: Context) -> str:
    ids = re.findall(r"<([^<>]*)>", value) or ([value.strip()] if value.strip() else [])
    return " ".join(f"<{ctx.pseudo.opaque_id(i)}@{ctx.pseudo.email_domain}>" for i in ids)


def scrub_date_header(value: str, ctx: Context) -> str | None:
    try:
        year = email.utils.parsedate_to_datetime(value).year
    except (TypeError, ValueError, IndexError, OverflowError):
        year = ctx.gen.year_of(value)
    if not year:
        return None
    ctx.tally.years.add(year)
    ctx.tally.counts["DATE"] += 1
    return email.utils.format_datetime(dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc))


def _header_items(msg: Message) -> list[tuple[str, str]]:
    items = []
    for name, value in msg.raw_items():
        try:
            items.append((name, str(msg.policy.header_fetch_parse(name, value))))
        except Exception:  # malformed header: fall back to the raw value
            items.append((name, str(value)))
    return items


def scrub_headers(msg: Message, ctx: Context, top: bool) -> None:
    keep = {h.lower() for h in ctx.cfg["email"]["keep_headers"]}
    new: list[tuple[str, str]] = []
    for name, value in _header_items(msg):
        low = name.lower()
        if not top and low not in MIME_HEADERS:
            continue
        if top and low not in keep and low not in MIME_HEADERS:
            continue
        if low in ADDRESS_HEADERS:
            new.append((name, scrub_addresses(value, ctx)))
        elif low in ID_HEADERS:
            new.append((name, scrub_msgids(value, ctx)))
        elif low in DATE_HEADERS:
            d = scrub_date_header(value, ctx)
            if d:
                new.append((name, d))
        elif low in VERBATIM_HEADERS:
            new.append((name, value))
        else:  # Subject, Thread-Topic, Content-Description ...
            new.append((name, ctx.scrubber.scrub(value, ctx.tally).text))
    for name in set(msg.keys()):
        del msg[name]
    for name, value in new:
        try:
            msg[name] = value
        except Exception:
            msg[name] = re.sub(r"[^\x20-\x7e]", "?", value)
    # file names in Content-Type/Content-Disposition parameters
    for header, param in (("Content-Type", "name"), ("Content-Disposition", "filename")):
        if msg.get(header) is not None:
            value = msg.get_param(param, header=header)
            if value:
                value = email.utils.collapse_rfc2231_value(value)
                msg.set_param(param, ctx.scrubber.scrub_name(value, ctx.tally), header=header)


# ---------------------------------------------------------------- bodies

def get_text(part: Message) -> tuple[str, str]:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "us-ascii"
    for cs in (charset, "utf-8", "windows-1252"):
        try:
            return payload.decode(cs), cs
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("latin-1"), "latin-1"


def set_text(part: Message, text: str, charset: str) -> None:
    try:
        data = text.encode(charset)
    except (UnicodeEncodeError, LookupError):
        charset = "utf-8"
        data = text.encode("utf-8")
        part.set_param("charset", "utf-8")
    set_bytes(part, data, text_part=True)


def set_bytes(part: Message, data: bytes, text_part: bool = False) -> None:
    cte = (part.get("Content-Transfer-Encoding") or "7bit").lower().strip()
    if cte not in ("base64", "quoted-printable"):
        if not text_part or not data.isascii() or any(len(line) > 990 for line in data.splitlines()):
            cte = "quoted-printable" if text_part else "base64"
    if cte == "base64":
        payload = base64.encodebytes(data).decode("ascii")
    elif cte == "quoted-printable":
        payload = quopri.encodestring(data).decode("ascii")
    else:
        payload = data.decode("ascii")
    del part["Content-Transfer-Encoding"]
    part["Content-Transfer-Encoding"] = cte
    part.set_payload(payload)


def scrub_html(html: str, ctx: Context) -> str:
    soup = BeautifulSoup(html, "html.parser")
    strings = [s for s in soup.find_all(string=True) if s.strip()]
    results = ctx.scrubber.scrub_many([str(s) for s in strings], ctx.tally)
    for s, r in zip(strings, results):
        if r.text != str(s):
            s.replace_with(Comment(r.text) if isinstance(s, Comment) else type(s)(r.text))
    for tag in soup.find_all(True):
        for attr, value in list(tag.attrs.items()):
            if isinstance(value, list):
                value = " ".join(value)
            low = attr.lower()
            if low in URL_ATTRS or low.startswith("data-") or "@" in value or "://" in value:
                tag[attr] = scrub_url_attr(value, ctx)
            elif low in TEXT_ATTRS:
                tag[attr] = ctx.scrubber.scrub(value, ctx.tally).text
    return soup.decode(formatter="html")


def scrub_url_attr(value: str, ctx: Context) -> str:
    v = value.strip()
    if v.lower().startswith("cid:"):
        return f"cid:{ctx.pseudo.opaque_id(v[4:])}@{ctx.pseudo.email_domain}"
    if v.lower().startswith("mailto:"):
        addr = v[7:].split("?")[0]
        ctx.tally.counts["EMAIL_ADDRESS"] += 1
        return "mailto:" + ctx.pseudo.email_address(addr)
    if v.startswith("#") or not v:
        return value
    if re.match(r"(?i)^(https?|ftp)://|^www\.", v):
        ctx.tally.counts["URL"] += 1
        return ctx.pseudo.token("URL", v)
    return ctx.scrubber.scrub(value, ctx.tally).text


def placeholder(part: Message, ctx: Context, item: str, reason: str) -> EmailMessage:
    fname = part.get_filename()
    shown = ctx.scrubber.scrub_name(email.utils.collapse_rfc2231_value(fname), ctx.tally) if fname else None
    ctype = part.get_content_type()
    size = len(part.get_payload(decode=True) or b"")
    ctx.quarantine(f"{item} ({shown or ctype})", reason, ctype, size)
    ph = EmailMessage(policy=part.policy if isinstance(part.policy, type(policy.default)) else policy.default)
    note = (f"[Attachment removed by Data Frenzy Local Scrubber: {shown or ctype}. "
            f"Reason: {reason}. It is listed in the de-identification report.]\n")
    if fname or part.get_content_disposition() == "attachment":
        ph.set_content(note, subtype="plain", disposition="attachment", filename=f"{shown or 'attachment'}.removed.txt")
    else:
        ph.set_content(note, subtype="plain")
    return ph


def _ext(part: Message) -> str:
    fname = part.get_filename() or ""
    return Path(fname).suffix.lower()


def scrub_leaf(part: Message, ctx: Context, item: str, depth: int, headers: bool = True) -> Message:
    """Scrub one non-multipart part in place, or return a replacement part.
    headers=False when the part is the message itself (its headers are already done).
    Attachment rows don't count toward the file's record/message totals."""
    ctype = part.get_content_type()
    ext = _ext(part)
    if headers:
        scrub_headers(part, ctx, top=False)
    records, messages = ctx.tally.records, ctx.tally.messages
    try:
        return _scrub_leaf_body(part, ctx, item, depth, ctype, ext)
    finally:
        ctx.tally.records, ctx.tally.messages = records, messages


def _scrub_leaf_body(part: Message, ctx: Context, item: str, depth: int, ctype: str, ext: str) -> Message:
    from .tabular import scrub_csv_bytes, scrub_xlsx_bytes

    try:
        if ext == ".eml" or ctype in ("application/eml",):
            data = part.get_payload(decode=True) or b""
            sep = detect_linesep(data)
            inner = parse(data, sep)
            scrub_message(inner, ctx, item + "/eml", depth + 1)
            set_bytes(part, serialize(inner, sep))
        elif ext in (".csv", ".tsv") or ctype in ("text/csv", "text/tab-separated-values"):
            data = part.get_payload(decode=True) or b""
            set_bytes(part, scrub_csv_bytes(data, ctx, item, tsv=(ext == ".tsv" or "tab" in ctype)))
        elif ext == ".xlsx" or ctype == XLSX_MIME:
            data = part.get_payload(decode=True) or b""
            set_bytes(part, scrub_xlsx_bytes(data, ctx, item))
        elif ctype == "text/html":
            text, cs = get_text(part)
            set_text(part, scrub_html(text, ctx), cs)
        elif part.get_content_maintype() == "text":
            text, cs = get_text(part)
            set_text(part, ctx.scrubber.scrub(text, ctx.tally).text, cs)
        elif ctype in ("application/ms-tnef",) or ext == ".dat":
            return placeholder(part, ctx, item, "Outlook TNEF (winmail.dat) attachment cannot be scrubbed")
        elif part.get_content_maintype() in ("audio", "video") or ext in MEDIA_EXT:
            return placeholder(part, ctx, item, "audio/video contains voiceprints (biometric identifier)")
        elif part.get_content_maintype() == "image" or ext in IMAGE_EXT:
            return placeholder(part, ctx, item, "image attachment (face/OCR redaction arrives in Phase 3)")
        else:
            return placeholder(part, ctx, item, f"attachment type {ext or ctype} is not supported yet")
    except Exception as exc:  # unreadable/encrypted attachment
        return placeholder(part, ctx, item, f"attachment could not be opened ({type(exc).__name__})")
    return part


MEDIA_EXT = {".mp3", ".wav", ".m4a", ".mp4", ".mov", ".aac", ".ogg", ".oga", ".flac", ".wma", ".avi", ".mkv",
             ".webm", ".amr", ".3gp", ".caf", ".aiff", ".aif", ".opus", ".m4v", ".wmv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".webp"}


def scrub_message(msg: Message, ctx: Context, item: str, depth: int = 0, top: bool = True) -> None:
    if depth > MAX_DEPTH:
        raise ValueError("message nesting too deep")
    if top:
        scrub_headers(msg, ctx, top=True)
    if msg.preamble:
        msg.preamble = ctx.scrubber.scrub(msg.preamble, ctx.tally).text
    if msg.epilogue:
        msg.epilogue = ctx.scrubber.scrub(msg.epilogue, ctx.tally).text
    if msg.get_content_type() == "message/rfc822":
        for inner in msg.get_payload():
            scrub_message(inner, ctx, item + "/msg", depth + 1, top=True)
        return
    if not msg.is_multipart():
        replacement = scrub_leaf(msg, ctx, item, depth, headers=not top)
        if replacement is not None and replacement is not msg:
            # top-level single-part message that can't be scrubbed: swap its body
            msg.set_payload(replacement.get_payload())
            for h in ("Content-Type", "Content-Transfer-Encoding", "Content-Disposition"):
                del msg[h]
                if replacement.get(h):
                    msg[h] = replacement[h]
        return
    parts = msg.get_payload()
    for i, part in enumerate(parts):
        sub_item = f"{item}#part{i + 1}"
        if part.is_multipart() or part.get_content_type() == "message/rfc822":
            scrub_headers(part, ctx, top=False)
            scrub_message(part, ctx, sub_item, depth + 1, top=False)
        else:
            parts[i] = scrub_leaf(part, ctx, sub_item, depth)


def leaf_signature(msg: Message) -> list[str]:
    return [p.get_content_type() for p in msg.walk() if not p.is_multipart()]


def message_text(msg: Message) -> str:
    """All human-readable text of a (scrubbed) message, for residual detection."""
    chunks = []
    for name in ("From", "To", "Cc", "Bcc", "Reply-To", "Sender", "Subject", "Thread-Topic"):
        for v in msg.get_all(name) or []:
            chunks.append(f"{name}: {v}")
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype == "text/html":
            chunks.append(BeautifulSoup(get_text(part)[0], "html.parser").get_text("\n"))
        elif part.get_content_maintype() == "text" and ctype not in ("text/csv", "text/tab-separated-values"):
            chunks.append(get_text(part)[0])
    return "\n".join(chunks)


def verify_message_bytes(data: bytes, expected_leaves: list[str], ctx: Context, where: str) -> None:
    try:
        msg = parse(data, detect_linesep(data))
    except Exception as exc:
        ctx.issue(f"{where}: scrubbed message does not parse ({type(exc).__name__})")
        return
    defects = [type(d).__name__ for p in msg.walk() for d in p.defects]
    if defects:
        ctx.issue(f"{where}: MIME defects after scrubbing: {sorted(set(defects))}")
    if leaf_signature(msg) != expected_leaves:
        ctx.issue(f"{where}: MIME structure changed during scrubbing")
    for p in msg.walk():
        if not p.is_multipart() and p.get_content_type() != "message/rfc822":
            try:
                p.get_payload(decode=True)
            except Exception as exc:
                ctx.issue(f"{where}: part does not decode ({type(exc).__name__})")
    ctx.residual(message_text(msg), where)


def scrub_message_bytes(data: bytes, ctx: Context, item: str, linesep: str | None = None) -> bytes:
    sep = linesep or detect_linesep(data)
    msg = parse(data, sep)
    scrub_message(msg, ctx, item)
    out = serialize(msg, sep)
    verify_message_bytes(out, leaf_signature(msg), ctx, item)
    ctx.tally.records += 1
    ctx.tally.messages += 1
    return out


def process_eml(src: Path, dst: Path, ctx: Context) -> None:
    data = src.read_bytes()
    out = scrub_message_bytes(data, ctx, ctx.out_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    # file-level check: re-open what was written
    written = dst.read_bytes()
    if written != out:
        ctx.issue(f"{ctx.out_rel}: output file differs from what was written")
