"""Outlook .pst -> mbox with libpff (pypff), for Windows where readpst isn't available.

Each Outlook folder becomes one mbox file (<folder path>/mbox), like readpst -r.
Messages are rebuilt from their original Internet headers when Outlook kept them,
plus the plain-text and HTML bodies and attachments.
"""

from __future__ import annotations

import re
from email import policy
from email.message import EmailMessage
from email.parser import HeaderParser
from pathlib import Path

KEEP = ("From", "To", "Cc", "Bcc", "Reply-To", "Subject", "Date", "Message-ID", "In-Reply-To", "References")


def available() -> bool:
    try:
        import pypff  # noqa: F401
    except ImportError:
        return False
    return True


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for enc in ("utf-8", "utf-16-le", "cp1252"):
            try:
                return value.decode(enc)
            except UnicodeDecodeError:
                continue
        return value.decode("latin-1")
    return str(value)


def _message(m) -> bytes:
    msg = EmailMessage(policy=policy.default)
    headers = HeaderParser(policy=policy.default).parsestr(_text(getattr(m, "transport_headers", None)) or "")
    for h in KEEP:
        v = headers.get(h)
        if v:
            try:
                msg[h] = str(v)
            except Exception:
                pass
    if "Subject" not in msg and getattr(m, "subject", None):
        msg["Subject"] = _text(m.subject)
    if "From" not in msg and getattr(m, "sender_name", None):
        msg["From"] = _text(m.sender_name)
    plain = _text(getattr(m, "plain_text_body", None))
    html = _text(getattr(m, "html_body", None))
    msg.set_content(plain or re.sub(r"<[^>]+>", " ", html) or "")
    if html:
        msg.add_alternative(html, subtype="html")
    for i in range(getattr(m, "number_of_attachments", 0) or 0):
        try:
            a = m.get_attachment(i)
            size = a.get_size()
            data = a.read_buffer(size) if size else b""
            name = _text(getattr(a, "name", None)) or f"attachment-{i + 1}"
            msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
        except Exception:
            continue
    return msg.as_bytes()


def _walk(folder, path: list[str], out: Path, written: list[Path]) -> None:
    name = _text(getattr(folder, "name", None)) or "folder"
    here = path + [re.sub(r'[<>:"/\\|?*]', "_", name)]
    n = getattr(folder, "number_of_sub_messages", 0) or 0
    if n:
        target = out.joinpath(*here) / "mbox"
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as fh:
            for i in range(n):
                try:
                    data = _message(folder.get_sub_message(i))
                except Exception:
                    continue
                fh.write(b"From MAILER-DAEMON Thu Jan  1 00:00:00 1970\n")
                fh.write(re.sub(rb"(?m)^From ", b">From ", data.replace(b"\r\n", b"\n")))
                fh.write(b"\n\n")
        written.append(target)
    for j in range(getattr(folder, "number_of_sub_folders", 0) or 0):
        _walk(folder.get_sub_folder(j), here, out, written)


def convert(pst: Path, out: Path) -> list[Path]:
    import pypff
    f = pypff.file()
    f.open(str(pst))
    try:
        written: list[Path] = []
        _walk(f.get_root_folder(), [], out, written)
        return written
    finally:
        f.close()
