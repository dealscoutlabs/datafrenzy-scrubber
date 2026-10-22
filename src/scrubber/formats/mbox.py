"""mbox mailboxes, streamed message by message.

Large mailboxes are split into byte-range shards (each starting at a "From "
line) that are scrubbed in parallel and concatenated in order. Each shard
checkpoints its progress so an interrupted run picks up where it stopped.
"""

from __future__ import annotations

import re
import shutil
import time
from email.parser import BytesHeaderParser
from email import policy
from pathlib import Path
from typing import Iterator

from .base import Context
from .email_ import scrub_message_bytes

FROM_LINE = b"From "


def shard_bounds(path: Path, shard_bytes: int) -> list[tuple[int, int]]:
    size = path.stat().st_size
    if size <= shard_bytes:
        return [(0, size)]
    bounds = [0]
    with open(path, "rb") as f:
        pos = shard_bytes
        while pos < size:
            f.seek(pos)
            found = None
            carry = b""
            base = pos
            while True:
                block = f.read(1 << 20)
                if not block:
                    break
                buf = carry + block
                idx = buf.find(b"\n" + FROM_LINE)
                if idx >= 0:
                    found = base - len(carry) + idx + 1
                    break
                carry = buf[-6:]
                base += len(block)
            if found is None or found >= size:
                break
            bounds.append(found)
            pos = found + shard_bytes
    bounds.append(size)
    return list(zip(bounds[:-1], bounds[1:]))


def iter_messages(f, start: int, stop: int) -> Iterator[bytes]:
    """Messages (without their From_ line) whose From_ line starts in [start, stop)."""
    f.seek(start)
    pos = start
    cur: bytearray | None = None
    while True:
        line = f.readline()
        if not line:
            break
        if line.startswith(FROM_LINE):
            if pos >= stop:
                break
            if cur is not None:
                yield _trim(cur)
            cur = bytearray()
        elif cur is not None:
            cur += line
        pos += len(line)
    if cur is not None:
        yield _trim(cur)


def _trim(msg: bytearray) -> bytes:
    data = bytes(msg)
    if data.endswith(b"\r\n\r\n"):
        return data[:-2]
    if data.endswith(b"\n\n"):
        return data[:-1]
    return data


_DATE_YEAR = re.compile(rb"(?mi)^Date:[^\n]*?\b((?:19|20)\d\d)\b")


def from_line(scrubbed: bytes) -> bytes:
    m = _DATE_YEAR.search(scrubbed)
    year = int(m.group(1)) if m else 1970
    stamp = time.strftime("%a %b %d %H:%M:%S %Y", time.struct_time((year, 1, 1, 0, 0, 0, 0, 1, 0)))
    return f"From MAILER-DAEMON {stamp}\n".encode()


def escape_from(body: bytes) -> bytes:
    return re.sub(rb"(?m)^From ", b">From ", body)


def process_shard(src: Path, part: Path, ctx: Context, shard: int, start: int, stop: int,
                  resume: tuple[int, int] | None, checkpoint, every: int) -> int:
    """Scrub one shard into a part file. Returns the number of messages written."""
    part.parent.mkdir(parents=True, exist_ok=True)
    progress = 0
    mode = "w+b"
    if resume and resume[0] > 0 and part.exists():
        progress, offset = resume
        mode = "r+b"
    with open(src, "rb") as f, open(part, mode) as out:
        if mode == "r+b":
            out.seek(offset)
            out.truncate()
        n = -1
        for n, raw in enumerate(iter_messages(f, start, stop)):
            if n < progress:
                continue
            item = f"{ctx.out_rel} message {shard}.{n + 1}"
            try:
                scrubbed = scrub_message_bytes(raw, ctx, item, linesep="\n")
            except Exception as exc:
                ctx.issue(f"{item}: could not be processed ({type(exc).__name__}); left out of the output")
                continue
            body = escape_from(scrubbed)
            if not body.endswith(b"\n"):
                body += b"\n"
            out.write(from_line(scrubbed) + body + b"\n")
            if (n + 1) % every == 0 and checkpoint:
                out.flush()
                checkpoint(n + 1, out.tell())
        out.flush()
    return n + 1


def assemble(parts: list[Path], dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".assembling")
    with open(tmp, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out, 1 << 22)
    tmp.replace(dst)


def verify_mbox(dst: Path, expected: int, ctx: Context) -> None:
    """Re-open the assembled mailbox: every message parses and the count matches.
    (Each message's text was already re-checked for residual identifiers from
    the exact bytes written.)"""
    parser = BytesHeaderParser(policy=policy.default)
    count = 0
    with open(dst, "rb") as f:
        size = dst.stat().st_size
        for raw in iter_messages(f, 0, size + 1):
            try:
                msg = parser.parsebytes(raw)
                if msg.get("From") is None and msg.get("Date") is None and msg.get("Message-ID") is None:
                    ctx.issue(f"{ctx.out_rel}: message {count + 1} has no headers after scrubbing")
            except Exception as exc:
                ctx.issue(f"{ctx.out_rel}: message {count + 1} does not parse ({type(exc).__name__})")
            count += 1
    if count != expected:
        ctx.issue(f"{ctx.out_rel}: {count} messages in output, expected {expected}")


def looks_like_mbox(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(5)
    except OSError:
        return False
    return head == FROM_LINE
