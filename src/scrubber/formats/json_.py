"""JSON, JSON Lines and Slack exports.

String values are scrubbed in place; keys, nesting and list lengths never
change. Key names steer the rule for their values (an "email" key holds an
email address, a "created" key holds a timestamp...), reusing the spreadsheet
column rules. Slack IDs (U012ABC...) become consistent opaque IDs so mentions,
threads and channel membership still line up.
"""

from __future__ import annotations

import datetime as dt
import hmac
import json
import re
from typing import Any

from .base import Context
from .columns import COLUMN_ENTITY, classify_header

SLACK_ID = re.compile(r"^[UWBCGDTEFSARLKQ][A-Z0-9]{7,14}$")
SLACK_TS = re.compile(r"^\d{9,10}\.\d{6}$")
SLACK_REF = re.compile(r"<([@#!])([A-Za-z0-9^]+)(?:\|([^>]*))?>|<((?:mailto:|https?://|tel:|ftp://)[^|>\s]+)(?:\|([^>]*))?>")
EPOCH_KEY = re.compile(r"(?:^|_)(ts|time|timestamp|created|updated|modified|date|at|expires?|edited|last_read|latest|"
                       r"deleted|start|end|joined|last_active|birthday|dob)$", re.I)
TS_KEYS = {"ts", "thread_ts", "latest_reply", "event_ts", "last_read", "parent_ts", "latest", "message_ts",
           "edited_ts", "item_ts", "pinned_ts"}
KEEP_KEYS = {"type", "subtype", "style", "mimetype", "filetype", "pretty_type", "mode", "color", "emoji", "unicode",
             "skin_tone", "lang", "language", "tz_offset", "is_bot", "deleted", "count", "format", "level",
             "status_emoji", "always_show_in_profile", "fallback_color", "bot_id_type", "external_type",
             "block_id", "action_id"}
PERSON_KEYS = {"real_name", "real_name_normalized", "display_name", "display_name_normalized", "first_name",
               "last_name", "full_name", "author_name", "user_name_full", "fullname", "given_name", "family_name",
               "patient_name", "member_name", "contact_name", "owner_name", "sender_name", "recipient_name"}
HANDLE_KEYS = {"username", "user_name", "handle", "screen_name", "login", "skype"}
BLANK_KEYS = {"tz", "tz_label", "avatar_hash", "status_text_canonical"}
IMAGE_KEY = re.compile(r"^(image_\d+|image_original|image_\d+x\d+|thumb_\d+\w*|avatar\w*|icon\w*)$")
NAME_CONTEXT_KEEP = {"reactions", "emoji", "icons"}
NUMERIC_ID_TYPES = {"mrn", "ssn", "account", "health_plan", "phone", "fax", "other_id", "license", "device",
                    "vehicle", "biometric"}
STRING_TOKEN_TYPES = NUMERIC_ID_TYPES | {"ip", "person_name", "organization", "address", "city", "url"}


def _ts_key(key: str) -> bool:
    return key in TS_KEYS or key.endswith("_ts")


class JsonScrubber:
    """Walks one JSON document; free-text strings are batched for the language model."""

    def __init__(self, ctx: Context, slack: bool = False, kind: str | None = None, channel_names: dict | None = None):
        self.ctx = ctx
        self.slack = slack
        self.kind = kind                    # users | channels | messages | None
        self.channel_names = channel_names if channel_names is not None else {}
        self.pending: list[tuple[Any, Any, str]] = []

    # ---------------------------------------------------------- mapping helpers
    def slack_id(self, value: str) -> str:
        return f"{value[0]}Z{self.ctx.pseudo.opaque_id('slack:' + value, 10).upper()}"

    def _year(self, seconds: float) -> int:
        return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).year

    def _jan1(self, year: int) -> int:
        return int(dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc).timestamp())

    def slack_ts(self, value: str) -> str:
        """Message timestamps double as message IDs: map each to a unique value on
        Jan 1 of the same year (consistent, so thread links survive)."""
        year = self._year(float(value))
        self.ctx.tally.years.add(year)
        h = int(hmac.new(self.ctx.pseudo.key, b"ts\x00" + value.encode(), "sha256").hexdigest(), 16)
        return f"{self._jan1(year) + h % 86400}.{(h >> 20) % 1_000_000:06d}"

    def epoch(self, value: float | int | str):
        """Any other timestamp -> Jan 1 of its year, same type and unit."""
        num = float(value)
        ms = num > 1e11
        seconds = num / 1000 if ms else num
        if not (1e8 < seconds < 4.2e9):
            return value
        year = self._year(seconds)
        self.ctx.tally.years.add(year)
        self.ctx.tally.counts["DATE"] += 1
        out = self._jan1(year) * (1000 if ms else 1)
        if isinstance(value, str):
            return str(out) if re.fullmatch(r"\d+", value) else f"{out}.000000"
        return type(value)(out)

    def token(self, etype: str, value: str) -> str:
        self.ctx.tally.counts[etype] += 1
        return self.ctx.pseudo.token(etype, value)

    def channel_name(self, name: str) -> str:
        m = re.fullmatch(r"mpdm-(.+)-(\d+)", name)
        if m and name not in self.channel_names:
            # group DM names are made of the members' usernames
            users = m.group(1).split("--")
            self.channel_names[name] = "mpdm-" + "--".join(self.token("OTHER_ID", u).lower() for u in users) \
                + f"-{m.group(2)}"
        if name not in self.channel_names:
            self.channel_names[name] = self.ctx.scrubber.scrub_name(name.replace("-", " "), self.ctx.tally).replace(" ", "-")
        return self.channel_names[name]

    def slack_text(self, text: str) -> str:
        def ref(m: re.Match) -> str:
            if m.group(1):
                sigil, ident, label = m.group(1), m.group(2), m.group(3)
                if sigil == "@":
                    return f"<@{self.slack_id(ident)}>"
                if sigil == "#":
                    return f"<#{self.slack_id(ident)}|{self.channel_name(label)}>" if label else f"<#{self.slack_id(ident)}>"
                if ident.startswith("subteam^"):
                    sid = ident.split("^", 1)[1]
                    return f"<!subteam^{self.slack_id(sid)}>"
                return m.group(0)  # <!here>, <!channel>, <!everyone>
            target, label = m.group(4), m.group(5)
            if target.startswith("mailto:"):
                addr = self.ctx.pseudo.email_address(target[7:])
                self.ctx.tally.counts["EMAIL_ADDRESS"] += 1
                return f"<mailto:{addr}|{addr}>"
            if target.startswith("tel:"):
                return f"<tel:{self.token('PHONE_NUMBER', target[4:])}>"
            tok = self.token("URL", target)
            return f"<https://scrubbed.invalid/{tok}>"
        return SLACK_REF.sub(ref, text)

    # ---------------------------------------------------------- walking
    def walk(self, node: Any, key: str | None = None, path: tuple = ()) -> Any:
        if isinstance(node, dict):
            for k in list(node):
                node[k] = self.walk(node[k], k, path + (k,))
            return node
        if isinstance(node, list):
            for i, item in enumerate(node):
                node[i] = self.walk(item, key, path)
            return node
        if isinstance(node, bool) or node is None:
            return node
        if isinstance(node, (int, float)):
            return self.number(node, key or "")
        if isinstance(node, str):
            return self.string(node, key or "", path)
        return node

    def number(self, value, key: str):
        k = key.lower()
        if EPOCH_KEY.search(k) or k in TS_KEYS:
            return self.epoch(value)
        ctype = classify_header(k)
        if ctype == "age":
            if value >= 90:
                self.ctx.tally.counts["AGE_OVER_89"] += 1
                return "90+"
            return value
        if ctype == "zip":
            self.ctx.tally.counts["ZIP_CODE"] += 1
            return self.ctx.gen.zip_text(str(int(value)))
        if ctype in NUMERIC_ID_TYPES and isinstance(value, int):
            # an identifier stored as a number stays a number: 9 followed by the token's sequence
            tok = self.token(COLUMN_ENTITY[ctype], str(value))
            return int("9" + tok.rsplit("_", 1)[1].zfill(9))
        return value

    def string(self, value: str, key: str, path: tuple) -> str:
        if not value.strip():
            return value
        k = key.lower()
        if k in KEEP_KEYS or (k == "name" and any(p in NAME_CONTEXT_KEEP for p in path)):
            return value
        if self.slack and SLACK_ID.match(value) and re.search(r"\d", value):
            return self.slack_id(value)
        if SLACK_TS.match(value) and (_ts_key(k) or self.slack):
            return self.slack_ts(value)
        if (EPOCH_KEY.search(k) or k in TS_KEYS) and re.fullmatch(r"\d{9,13}(\.\d+)?", value):
            return self.epoch(value)
        if k in BLANK_KEYS:
            return ""
        if IMAGE_KEY.match(k) or (value.startswith(("http://", "https://")) and " " not in value):
            return f"https://scrubbed.invalid/{self.token('URL', value)}"
        if k in PERSON_KEYS:
            return self.token("PERSON", value)
        if k in HANDLE_KEYS or (k == "name" and (self.kind == "users" or "user_profile" in path)):
            return self.token("OTHER_ID", value)
        if self.kind == "channels" and k == "name" and len(path) == 1:
            return self.channel_name(value)
        if k == "name" and "files" in path:
            return self.ctx.scrubber.scrub_name(value, self.ctx.tally)
        ctype = None if k in ("name", "text", "title", "value") else classify_header(k)
        if ctype in ("email",) and "@" in value:
            self.ctx.tally.counts["EMAIL_ADDRESS"] += 1
            return self.ctx.pseudo.token("EMAIL_ADDRESS", value)
        if ctype in STRING_TOKEN_TYPES:
            return self.token(COLUMN_ENTITY[ctype], value)
        if ctype == "zip":
            self.ctx.tally.counts["ZIP_CODE"] += 1
            return self.ctx.gen.zip_text(value)
        if ctype in ("dob", "date") and self.ctx.gen.has_date_element(value):
            self.ctx.tally.counts["DATE_BIRTH" if ctype == "dob" else "DATE"] += 1
            y = self.ctx.gen.year_of(value)
            if y:
                self.ctx.tally.years.add(y)
            return self.ctx.gen.date_text(value, birth=(ctype == "dob"))
        if ctype == "age":
            return str(self.ctx.gen.age(value))
        if ctype in ("state", "country"):
            return value
        if self.slack:
            value = self.slack_text(value)
        self.pending.append((None, None, value))
        return _Pending(len(self.pending) - 1)

    def flush(self, root: Any) -> Any:
        """Scrub all batched free text and put the results in place."""
        if not self.pending:
            return root
        texts = [p[2] for p in self.pending]
        results = self.ctx.scrubber.scrub_joined(texts, self.ctx.tally)
        self.pending = []
        return _resolve_pending(root, [r.text for r in results])


class _Pending(int):
    """Placeholder for a string waiting for batched scrubbing."""


def _resolve_pending(node: Any, values: list[str]) -> Any:
    if isinstance(node, _Pending):
        return values[int(node)]
    if isinstance(node, dict):
        for k in node:
            node[k] = _resolve_pending(node[k], values)
    elif isinstance(node, list):
        for i in range(len(node)):
            node[i] = _resolve_pending(node[i], values)
    return node


def scrub_json_obj(obj: Any, ctx: Context, *, slack: bool = False, kind: str | None = None,
                   channel_names: dict | None = None) -> Any:
    js = JsonScrubber(ctx, slack=slack, kind=kind, channel_names=channel_names)
    return js.flush(js.walk(obj))


# ---------------------------------------------------------------- structure checks

def shape(obj: Any) -> Any:
    """Keys, nesting and list lengths; scalars collapse to a kind (scrubbed numbers may become strings)."""
    if isinstance(obj, dict):
        return {k: shape(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shape(v) for v in obj]
    if isinstance(obj, bool):
        return "bool"
    if obj is None:
        return "null"
    return "scalar"


def strings(obj: Any, key: str = "") -> list[str]:
    """Free-text-ish string values, for residual detection."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += strings(v, k)
    elif isinstance(obj, list):
        for v in obj:
            out += strings(v, key)
    elif isinstance(obj, str) and obj.strip():
        k = key.lower()
        if k in KEEP_KEYS or _ts_key(k) or SLACK_TS.match(obj) or SLACK_ID.match(obj):
            return out
        out.append(obj)
    return out


def detect_indent(text: str) -> int | None:
    m = re.search(r"^[\[{]\s*\n( +|\t)", text)
    if not m:
        return None
    return len(m.group(1)) if m.group(1) != "\t" else 1


def dumps(obj: Any, indent: int | None, ascii_only: bool) -> str:
    return json.dumps(obj, indent=indent, ensure_ascii=ascii_only, separators=None if indent else (",", ":"))


def verify_json(original_shape: Any, out_text: str, ctx: Context, item: str) -> None:
    try:
        obj = json.loads(out_text)
    except ValueError as exc:
        ctx.issue(f"{item}: scrubbed JSON does not parse ({exc.__class__.__name__})")
        return
    if shape(obj) != original_shape:
        ctx.issue(f"{item}: JSON structure changed during scrubbing")
    texts = strings(obj)
    for i in range(0, len(texts), 2000):
        ctx.residual("\n\n".join(texts[i:i + 2000]), item)


def scrub_json_text(text: str, ctx: Context, item: str, slack: bool = False, kind: str | None = None,
                    channel_names: dict | None = None) -> str:
    obj = json.loads(text)
    before = shape(obj)
    out_obj = scrub_json_obj(obj, ctx, slack=slack, kind=kind, channel_names=channel_names)
    out = dumps(out_obj, detect_indent(text), ascii_only="\\u" in text and not re.search(r"[^\x00-\x7f]", text))
    if text.endswith("\n"):
        out += "\n"
    verify_json(before, out, ctx, item)
    ctx.tally.records += len(out_obj) if isinstance(out_obj, list) else 1
    return out


def scrub_jsonl_text(text: str, ctx: Context, item: str) -> str:
    lines = text.splitlines(keepends=True)
    out = []
    for n, line in enumerate(lines):
        if not line.strip():
            out.append(line)
            continue
        end = line[len(line.rstrip("\r\n")):]
        out.append(scrub_json_text(line.rstrip("\r\n"), ctx, f"{item} line {n + 1}") + end)
    return "".join(out)


def decode(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-16"):
        if enc == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", errors="replace"), "cp1252"


def scrub_json_bytes(data: bytes, ctx: Context, item: str, jsonl: bool = False) -> bytes:
    text, enc = decode(data)
    out = scrub_jsonl_text(text, ctx, item) if jsonl else scrub_json_text(text, ctx, item)
    if enc == "utf-8-sig" and data.startswith(b"\xef\xbb\xbf"):
        return b"\xef\xbb\xbf" + out.encode("utf-8")
    return out.encode("utf-8" if enc == "utf-8-sig" else enc)


def process_json(src, dst, ctx: Context, jsonl: bool = False) -> None:
    from pathlib import Path
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not jsonl:
        dst.write_bytes(scrub_json_bytes(Path(src).read_bytes(), ctx, ctx.out_rel))
        return
    # JSON Lines: streamed line by line
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        for n, raw in enumerate(fin):
            line = raw.decode("utf-8", errors="replace")
            body = line.rstrip("\r\n")
            if body.strip():
                body = scrub_json_text(body, ctx, f"{ctx.out_rel} line {n + 1}")
            fout.write((body + line[len(line.rstrip("\r\n")):]).encode("utf-8"))
