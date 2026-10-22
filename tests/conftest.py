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
