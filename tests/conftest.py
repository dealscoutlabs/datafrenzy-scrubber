import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from scrubber import netguard  # noqa: E402
from scrubber.config import load_config, load_zip3_restricted  # noqa: E402
from scrubber.detect.engine import Detector  # noqa: E402
from scrubber.formats.base import Context  # noqa: E402
from scrubber.pipeline import Paths, Runner  # noqa: E402
from scrubber.state import Store  # noqa: E402
from scrubber.transform.generalize import Generalizer  # noqa: E402
from scrubber.transform.pseudonym import Pseudonymizer  # noqa: E402
from scrubber.transform.scrub import TextScrubber  # noqa: E402

from fixtures.synth import Synth  # noqa: E402

# The suite itself must never touch the network either.
netguard.install()

if __import__("os").environ.get("SCRUBBER_DEBUG_RESIDUAL"):
    # print what verification flags (debug aid; never on by default)
    import scrubber.formats.base as _base
    _orig = _base.Context.residual

    def _debug_residual(self, text, where, page=None):
        for f in self.scrubber.detector.detect(text):
            if f.score >= float(self.cfg["verification"]["residual_threshold"]):
                print("RESIDUAL", where, f.etype, repr(f.text), f.source, "|", repr(text[max(0, f.start - 60):f.end + 40]))
        return _orig(self, text, where, page)
    _base.Context.residual = _debug_residual

REFERENCE_YEAR = 2026


@pytest.fixture(scope="session")
def cfg():
    return load_config(overrides={"reference_year": REFERENCE_YEAR})


@pytest.fixture(scope="session")
def detector(cfg):
    return Detector(cfg)


@pytest.fixture(scope="session")
def store(tmp_path_factory):
    return Store(tmp_path_factory.mktemp("state") / "state.db")


@pytest.fixture(scope="session")
def scrubber(cfg, detector, store):
    pseudo = Pseudonymizer(b"\x01" * 32, store, cfg)
    return TextScrubber(detector, pseudo, Generalizer(REFERENCE_YEAR, load_zip3_restricted()))


@pytest.fixture
def ctx(cfg, scrubber, store):
    return Context(cfg=cfg, scrubber=scrubber, store=store, rel="test/input.file", out_rel="test/output.file")


@pytest.fixture
def synth():
    return Synth(seed=20261021)


@pytest.fixture
def workspace(tmp_path):
    paths = Paths(tmp_path / "input", tmp_path / "output", tmp_path / "work", tmp_path / "state")
    paths.input.mkdir()
    return paths


@pytest.fixture
def run_pipeline(cfg):
    def _run(paths, workers=1, overrides=None, messages=None):
        c = load_config(overrides={"reference_year": REFERENCE_YEAR, **(overrides or {})})
        out = []
        runner = Runner(paths, c, workers=workers, echo=(messages.append if messages is not None else out.append))
        summary = runner.run()
        return runner, summary
    return _run


def person_secrets(p):
    """Strings from a synthetic person that must never survive scrubbing."""
    return {"names": [p.first, p.last] + p.relative.split(),
            "exact": [p.email, p.ssn, p.mrn, p.member_id, p.npi, p.phone, p.fax, p.street, p.username, p.claim]}


def assert_no_secrets(text: str, people, where=""):
    import re as _re
    digits = _re.sub(r"\D", "", text)
    for p in people:
        sec = person_secrets(p)
        for w in sec["names"]:
            assert not _re.search(rf"(?<![A-Za-z]){_re.escape(w)}(?![a-z])", text), f"name {w!r} leaked {where}"
        for v in sec["exact"]:
            assert v not in text, f"{v!r} leaked {where}"
        for ph in (p.phone, p.fax):
            assert _re.sub(r"\D", "", ph)[-7:] not in digits, f"phone {ph!r} leaked {where}"


@pytest.fixture
def harvested(cfg, store):
    """A scrubber that knows these people's names, as the pipeline's harvest step would."""
    from scrubber.detect.harvest import _person_rows, known_entries

    def make(people):
        rows = []
        for p in people:
            for r in _person_rows(p.name, "test") + _person_rows(p.relative, "test"):
                rows.append({"norm": r[0], "etype": r[1], "surface": r[2]})
        entries, full = known_entries(rows, set())
        pseudo = Pseudonymizer(b"\x03" * 32, store, cfg)
        pseudo.set_person_aliases(full)
        sc = TextScrubber(Detector(cfg, entries), pseudo, Generalizer(REFERENCE_YEAR, load_zip3_restricted()))
        return Context(cfg=cfg, scrubber=sc, store=store, rel="test/doc", out_rel="test/doc")
    return make
