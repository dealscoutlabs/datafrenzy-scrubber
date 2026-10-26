"""Zip archives, including Slack workspace exports.

Every member is scrubbed by type; members that can't be scrubbed are left out
and listed. Member timestamps are reset (they are dates). In a Slack export,
users/channels/conversations files are scrubbed with Slack-aware rules, and the
per-day message files (named by full date, e.g. general/2021-03-14.json) are
merged into one file per channel per year (general/2021-01-01.json), because
a file name holding the day and month is itself a date identifier.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .base import Context
from .dispatch import scrub_bytes
from .json_ import SLACK_ID, JsonScrubber, decode, detect_indent, dumps, scrub_json_obj, shape, strings

DAY_FILE = re.compile(r"^(\d{4})-\d{2}-\d{2}\.json$")
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
MAX_MEMBER = 4 << 30
SLACK_ROOT_KINDS = {"users.json": "users", "org_users.json": "users", "channels.json": "channels",
                    "groups.json": "channels", "mpims.json": "channels", "dms.json": "channels"}


def list_members(zin: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return [i for i in zin.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")
            and not PurePosixPath(i.filename).name.startswith("._")]


def slack_root(names: list[str]) -> str | None:
    """'' or 'folder/' if this zip is a Slack export, else None."""
    for n in names:
        p = PurePosixPath(n)
        if p.name in ("users.json", "channels.json") and len(p.parts) <= 2:
            return "" if len(p.parts) == 1 else p.parts[0] + "/"
    return None


def _info(name: str) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    return zi


def _unique(name: str, taken: set[str]) -> str:
    base, n = name, 2
    while name in taken:
        stem, dot, ext = base.rpartition(".")
        name = f"{stem}-{n}.{ext}" if dot and "/" not in ext else f"{base}-{n}"
        n += 1
    taken.add(name)
    return name


def _readable(info: zipfile.ZipInfo, ctx: Context, item: str) -> bool:
    if info.flag_bits & 0x1:
        ctx.quarantine(f"{item}", "password-protected zip member - not exported")
        return False
    if info.file_size > MAX_MEMBER:
        ctx.quarantine(f"{item}", "zip member larger than 4 GB - extract it into the input folder instead")
        return False
    return True


def scrub_zip(src: BinaryIO, dst: BinaryIO, ctx: Context, item: str, depth: int = 0) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
        members = list_members(zin)
        root = slack_root([m.filename for m in members])
        if root is not None:
            _scrub_slack(zin, zout, members, root, ctx, item)
            return
        taken: set[str] = set()
        for info in members:
            parts = info.filename.split("/")
            shown = "/".join(ctx.scrubber.scrub_name(p, ctx.tally) for p in parts)
            where = f"{item}/{shown}"
            if not _readable(info, ctx, where):
                continue
            records = ctx.tally.records
            try:
                out = scrub_bytes(info.filename, zin.read(info), ctx, where, depth=depth)
            except Exception as exc:
                ctx.quarantine(where, f"could not be opened ({type(exc).__name__}) - not exported")
                continue
            finally:
                ctx.tally.records = records
            if out is None:
                ctx.quarantine(where, f"file type {PurePosixPath(info.filename).suffix or 'unknown'} "
                                      "inside a zip is not supported - not exported")
                continue
            zout.writestr(_info(_unique(shown, taken)), out)
            ctx.tally.records += 1


def _scrub_slack(zin, zout, members, root: str, ctx: Context, item: str) -> None:
    channel_names: dict[str, str] = {}
    folder_names: dict[str, str] = {}
    taken: set[str] = set()
    js = JsonScrubber(ctx, slack=True, channel_names=channel_names)

    root_files = [m for m in members if PurePosixPath(m.filename[len(root):]).parent == PurePosixPath(".")]
    # channels first, so channel folder names map the same way everywhere
    root_files.sort(key=lambda m: (SLACK_ROOT_KINDS.get(PurePosixPath(m.filename).name) != "channels",
                                   m.filename))
    for info in root_files:
        name = PurePosixPath(info.filename).name
        where = f"{item}/{name}"
        if not name.endswith(".json"):
            _other_member(zin, zout, info, root, ctx, item, taken)
            continue
        text, _ = decode(zin.read(info))
        obj = json.loads(text)
        before = shape(obj)
        kind = SLACK_ROOT_KINDS.get(name)
        out = scrub_json_obj(obj, ctx, slack=True, kind=kind, channel_names=channel_names)
        if kind == "channels" and isinstance(obj, list):
            for orig, new in zip(_names(text), out):
                if isinstance(new, dict):
                    if new.get("name") and orig.get("name"):
                        folder_names[orig["name"]] = new["name"]
                    if orig.get("id") and new.get("id"):
                        folder_names.setdefault(orig["id"], new["id"])
        _check(before, out, ctx, where)
        zout.writestr(_info(_unique(root + name, taken)), dumps(out, detect_indent(text) or 4, False))
        ctx.tally.records += len(out) if isinstance(out, list) else 1

    # conversation folders: <folder>/<YYYY-MM-DD>.json -> <folder>/<YYYY>-01-01.json
    groups: "OrderedDict[tuple[str, str], list]" = OrderedDict()
    for info in sorted((m for m in members if m not in root_files), key=lambda m: m.filename):
        rel = PurePosixPath(info.filename[len(root):])
        m = DAY_FILE.match(rel.name)
        if m and len(rel.parts) == 2:
            groups.setdefault((rel.parts[0], m.group(1)), []).append(info)
        else:
            _other_member(zin, zout, info, root, ctx, item, taken)
    for (folder, year), infos in groups.items():
        new_folder = folder_names.get(folder)
        if new_folder is None:
            new_folder = js.slack_id(folder) if SLACK_ID.match(folder) and re.search(r"\d", folder) \
                else js.channel_name(folder)
            folder_names[folder] = new_folder
        name = _unique(f"{root}{new_folder}/{year}-01-01.json", taken)
        where = f"{item}/{new_folder}/{year}"
        ctx.tally.years.add(int(year))
        with zout.open(_info(name), "w", force_zip64=True) as fh:
            fh.write(b"[")
            first = True
            for info in infos:
                text, _ = decode(zin.read(info))
                msgs = json.loads(text)
                if not isinstance(msgs, list):
                    ctx.issue(f"{where}: day file is not a list of messages")
                    msgs = [msgs]
                before = shape(msgs)
                out = scrub_json_obj(msgs, ctx, slack=True, kind="messages", channel_names=channel_names)
                _check(before, out, ctx, where)
                for msg in out:
                    block = "\n".join("    " + line for line in json.dumps(msg, indent=4, ensure_ascii=False).splitlines())
                    fh.write((("\n" if first else ",\n") + block).encode("utf-8"))
                    first = False
                ctx.tally.records += len(out)
                ctx.tally.messages += len(out)
            fh.write(b"\n]\n" if not first else b"]\n")


def _names(text: str) -> list[dict]:
    try:
        obj = json.loads(text)
    except ValueError:
        return []
    return [o if isinstance(o, dict) else {} for o in obj] if isinstance(obj, list) else []


def _check(before, out, ctx: Context, where: str) -> None:
    if shape(out) != before:
        ctx.issue(f"{where}: JSON structure changed during scrubbing")
    from .tabular import needs_check
    texts = [t for t in strings(out) if needs_check(t)]
    for i in range(0, len(texts), 2000):
        ctx.residual("\n\n".join(texts[i:i + 2000]), where)


def _other_member(zin, zout, info, root, ctx: Context, item: str, taken: set[str]) -> None:
    parts = info.filename[len(root):].split("/")
    shown = root + "/".join(ctx.scrubber.scrub_name(p, ctx.tally) for p in parts)
    where = f"{item}/{shown}"
    if not _readable(info, ctx, where):
        return
    records = ctx.tally.records
    try:
        out = scrub_bytes(info.filename, zin.read(info), ctx, where)
    except Exception as exc:
        out = None
        ctx.quarantine(where, f"could not be opened ({type(exc).__name__}) - not exported")
        return
    finally:
        ctx.tally.records = records
    if out is None:
        ctx.quarantine(where, "file type not supported inside a Slack export - not exported")
        return
    zout.writestr(_info(_unique(shown, taken)), out)


def verify_zip(data_or_path, ctx: Context, item: str) -> None:
    try:
        with zipfile.ZipFile(data_or_path) as z:
            bad = z.testzip()
            if bad:
                ctx.issue(f"{item}: damaged member in scrubbed zip")
            for info in z.infolist():
                if info.filename.endswith(".json"):
                    with z.open(info) as fh:
                        try:
                            json.load(io.TextIOWrapper(fh, encoding="utf-8"))
                        except ValueError:
                            ctx.issue(f"{item}: a JSON member does not parse after scrubbing")
                if info.date_time != ZIP_EPOCH:
                    ctx.issue(f"{item}: a member kept its original timestamp")
    except zipfile.BadZipFile as exc:
        ctx.issue(f"{item}: scrubbed zip does not open ({exc})")


def scrub_zip_bytes(data: bytes, ctx: Context, item: str, depth: int = 0) -> bytes:
    out = io.BytesIO()
    scrub_zip(io.BytesIO(data), out, ctx, item, depth)
    verify_zip(io.BytesIO(out.getvalue()), ctx, item)
    return out.getvalue()


def process_zip(src: Path, dst: Path, ctx: Context) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as s, open(dst, "wb") as d:
        scrub_zip(s, d, ctx, ctx.out_rel)
    verify_zip(dst, ctx, ctx.out_rel)
