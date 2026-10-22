"""Configuration: built-in defaults merged with the user's config/scrubber.yaml."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from . import __version__

DEFAULT_CONFIG = Path(__file__).with_name("default_config.yaml")
ADDITIONS_FILE = "review-additions.yaml"
DATA_DIR = Path(os.environ.get("SCRUBBER_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))

# Lists under these top-level keys are appended to, not replaced.
_ADDITIVE = {"allowlist", "deny_list", "custom_patterns", "context_risk"}


def _merge(base: Any, override: Any, additive: bool) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            child_additive = additive or key in _ADDITIVE
            out[key] = _merge(base[key], value, child_additive) if key in base else value
        return out
    if additive and isinstance(base, list) and isinstance(override, list):
        return base + [v for v in override if v not in base]
    return override


def load_config(user_path: str | Path | None = None, overrides: dict | None = None) -> dict:
    cfg = yaml.safe_load(DEFAULT_CONFIG.read_text())
    if user_path and Path(user_path).is_file():
        user = yaml.safe_load(Path(user_path).read_text()) or {}
        if not isinstance(user, dict):
            raise ValueError(f"{user_path}: expected a YAML mapping at the top level")
        cfg = _merge(cfg, user, False)
    if user_path:
        # deny-list terms added on the review screen (kept apart so the user's file and comments stay intact)
        extra = Path(user_path).with_name(ADDITIONS_FILE)
        if extra.is_file():
            cfg = _merge(cfg, yaml.safe_load(extra.read_text()) or {}, False)
    if overrides:
        cfg = _merge(cfg, overrides, False)
    if cfg.get("reference_year") is None:
        cfg["reference_year"] = dt.date.today().year
    return cfg


def data_fingerprint() -> str:
    h = hashlib.sha256()
    for path in sorted(DATA_DIR.glob("*")):
        if path.is_file():
            h.update(path.name.encode())
            h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def config_hash(cfg: dict) -> str:
    """Hash of everything that changes scrubbing output: config, bundled data, version."""
    payload = json.dumps({"config": cfg, "data": data_fingerprint(), "version": __version__},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def load_zip3_restricted() -> set[str]:
    lines = (DATA_DIR / "zip3_restricted.csv").read_text().splitlines()[1:]
    return {line.split(",")[0].strip() for line in lines if line.strip()}


def load_name_list(filename: str) -> frozenset[str]:
    return frozenset(line.strip().upper() for line in (DATA_DIR / filename).read_text().splitlines() if line.strip())


def add_deny_terms(user_path: str | Path, etype: str, terms: list[str]) -> None:
    """Append terms to the review-additions file next to the user's config."""
    path = Path(user_path).with_name(ADDITIONS_FILE)
    data = yaml.safe_load(path.read_text()) if path.is_file() else None
    data = data or {}
    bucket = data.setdefault("deny_list", {}).setdefault(etype, [])
    for t in terms:
        t = t.strip()
        if t and t not in bucket:
            bucket.append(t)
    path.write_text("# Added on the review screen. Merged into the deny list on every run.\n"
                    + yaml.safe_dump(data, sort_keys=True, allow_unicode=True))


def remove_deny_term(user_path: str | Path, etype: str, term: str) -> None:
    path = Path(user_path).with_name(ADDITIONS_FILE)
    if not path.is_file():
        return
    data = yaml.safe_load(path.read_text()) or {}
    bucket = (data.get("deny_list") or {}).get(etype) or []
    if term in bucket:
        bucket.remove(term)
        path.write_text("# Added on the review screen. Merged into the deny list on every run.\n"
                        + yaml.safe_dump(data, sort_keys=True, allow_unicode=True))


def deny_additions(user_path: str | Path) -> dict:
    path = Path(user_path).with_name(ADDITIONS_FILE)
    if not path.is_file():
        return {}
    return (yaml.safe_load(path.read_text()) or {}).get("deny_list") or {}
