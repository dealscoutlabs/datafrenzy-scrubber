"""Plain text files (.txt, .md, .log), streamed in paragraph-aligned chunks."""

from __future__ import annotations

from pathlib import Path

from .base import Context

CHUNK = 256 * 1024


def detect_encoding(sample: bytes) -> str:
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    try:
        sample[:-4].decode("utf-8") if len(sample) >= CHUNK else sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def iter_chunks(path: Path, encoding: str):
    with open(path, encoding=encoding, errors="replace", newline="") as f:
        carry = ""
        while True:
            block = f.read(CHUNK)
            if not block:
                if carry:
                    yield carry
                return
            buf = carry + block
            cut = buf.rfind("\n\n")
            if cut < len(buf) // 2:
                cut = buf.rfind("\n")
            if cut <= 0:
                carry = buf
                if len(carry) > 4 * CHUNK:  # one enormous line
                    yield carry
                    carry = ""
                continue
            yield buf[: cut + 1]
            carry = buf[cut + 1:]


def process_text(src: Path, dst: Path, ctx: Context) -> None:
    with open(src, "rb") as f:
        encoding = detect_encoding(f.read(CHUNK))
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding=encoding, errors="replace", newline="") as out:
        for chunk in iter_chunks(src, encoding):
            out.write(ctx.scrubber.scrub(chunk, ctx.tally).text)
            ctx.tally.records += chunk.count("\n")
    # verification: re-open, decode, re-detect
    try:
        for chunk in iter_chunks(dst, encoding):
            ctx.residual(chunk, ctx.out_rel)
    except UnicodeError as exc:
        ctx.issue(f"{ctx.out_rel}: scrubbed text does not decode ({exc})")
