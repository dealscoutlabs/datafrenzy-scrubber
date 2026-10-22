import mailbox
import shutil
from email import policy

import pytest

from scrubber.formats import email_ as email_mod


def _make_mbox(path, synth, n):
    box = mailbox.mbox(path)
    people = [synth.person() for _ in range(8)]
    prev = None
    for i in range(n):
        a, b = people[i % 8], people[(i + 3) % 8]
        msg = synth.eml(a, b, reply_to=prev if i % 4 else None, csv_attachment=(i % 10 == 0))
        if i % 7 == 0:  # a body line starting with "From " must survive mbox escaping
            body = msg.get_body(("plain",))
            body.set_content(body.get_content() + "\nFrom here on, please use the portal.\n")
        box.add(mailbox.mboxMessage(msg.as_bytes(policy=policy.default)))
        prev = msg
    box.flush()
    box.close()
    return people


def _messages(path):
    box = mailbox.mbox(path, create=False)
    try:
        return [m for m in box]
    finally:
        box.close()


def test_mbox_scrubbed_message_by_message(workspace, synth, run_pipeline):
    people = _make_mbox(workspace.input / "Inbox.mbox", synth, 30)
    runner, summary = run_pipeline(workspace)
    out = workspace.released / "Inbox.mbox"
    assert out.exists(), summary
    msgs = _messages(out)
    assert len(msgs) == 30
    data = out.read_bytes()
    for p in people:
        for secret in (p.email, p.last, p.ssn, p.mrn, p.phone):
            assert secret.encode() not in data, secret
    assert all(m["From"] and "scrubbed.invalid" in m["From"] for m in msgs)


def test_sharded_output_identical_to_unsharded(tmp_path, synth, run_pipeline, workspace):
    _make_mbox(workspace.input / "big.mbox", synth, 40)
    size = (workspace.input / "big.mbox").stat().st_size
    run_pipeline(workspace, overrides={"mbox": {"shard_mb": 1, "checkpoint_every": 200}})
    whole = (workspace.released / "big.mbox").read_bytes()

    from scrubber.formats.mbox import shard_bounds
    shard_bytes = max(1, size // 5)
    assert len(shard_bounds(workspace.input / "big.mbox", shard_bytes)) >= 4

    other = type(workspace)(tmp_path / "in2", tmp_path / "out2", tmp_path / "work2", tmp_path / "state2")
    shutil.copytree(workspace.input, other.input)
    other.state.mkdir(parents=True)
    shutil.copy(workspace.state / "secret.key", other.state / "secret.key")
    import scrubber.pipeline as pl
    real = pl.mbox.shard_bounds
    pl.mbox.shard_bounds = lambda p, _b: real(p, shard_bytes)
    try:
        runner, _ = run_pipeline(other)
    finally:
        pl.mbox.shard_bounds = real
    assert len(runner.store.shards("big.mbox")) >= 4
    assert (other.released / "big.mbox").read_bytes() == whole


def test_interrupted_run_resumes_from_checkpoint(tmp_path, synth, run_pipeline, workspace, monkeypatch):
    _make_mbox(workspace.input / "Sent.mbox", synth, 25)
    reference = type(workspace)(tmp_path / "ri", tmp_path / "ro", tmp_path / "rw", tmp_path / "rs")
    shutil.copytree(workspace.input, reference.input)
    run_pipeline(reference)
    expected = (reference.released / "Sent.mbox").read_bytes()

    workspace.state.mkdir(parents=True, exist_ok=True)
    shutil.copy(reference.state / "secret.key", workspace.state / "secret.key")
    real = email_mod.scrub_message_bytes
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 13:
            raise KeyboardInterrupt  # simulates the computer shutting down mid-mailbox
        return real(*a, **k)

    import scrubber.formats.mbox as mbox_mod
    monkeypatch.setattr(mbox_mod, "scrub_message_bytes", flaky)
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(workspace, overrides={"mbox": {"shard_mb": 256, "checkpoint_every": 5}})
    monkeypatch.setattr(mbox_mod, "scrub_message_bytes", real)
    runner, _ = run_pipeline(workspace, overrides={"mbox": {"shard_mb": 256, "checkpoint_every": 5}})
    assert (workspace.released / "Sent.mbox").read_bytes() == expected
