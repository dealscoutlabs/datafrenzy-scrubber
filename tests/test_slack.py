import io
import json
import re
import zipfile

from conftest import assert_no_secrets
from fixtures.synth_docs import SlackWorkspace
from scrubber.formats.archive import ZIP_EPOCH, scrub_zip_bytes
from scrubber.formats.json_ import shape


def _scrub(synth, harvested, days=3):
    people = [synth.person() for _ in range(5)]
    ws = SlackWorkspace(synth, people)
    data, meta = ws.zip_bytes(days=days)
    ctx = harvested(people)
    out = scrub_zip_bytes(data, ctx, "slack.zip")
    z = zipfile.ZipFile(io.BytesIO(out))
    return people, ws, meta, ctx, z


def _load(z, name):
    return json.loads(z.read(name))


def test_no_identifiers_anywhere(synth, harvested):
    people, ws, meta, ctx, z = _scrub(synth, harvested)
    everything = "\n".join(z.read(n).decode() for n in z.namelist()) + "\n".join(z.namelist())
    assert_no_secrets(everything, people, "in slack export")
    for uid in ws.uid.values():
        assert uid not in everything
    assert ctx.tally.issues == []


def test_structure_preserved(synth, harvested):
    people, ws, meta, ctx, z = _scrub(synth, harvested)
    src = meta["files"]
    for name in ("users.json", "channels.json", "dms.json", "mpims.json", "integration_logs.json"):
        assert shape(_load(z, name)) == shape(json.loads(src[name])), name
    chan = _load(z, "channels.json")[0]["name"]
    merged = _load(z, f"{chan}/2022-01-01.json")
    day_files = sorted(n for n in src if n.startswith(meta["channel"] + "/"))
    originals = [m for n in day_files for m in json.loads(src[n])]
    assert len(merged) == len(originals)
    assert [shape(m) for m in merged] == [shape(m) for m in originals]
    assert all(zi.date_time == ZIP_EPOCH for zi in z.infolist())
    assert z.testzip() is None


def test_ids_mentions_and_threads_stay_linked(synth, harvested):
    people, ws, meta, ctx, z = _scrub(synth, harvested)
    users = _load(z, "users.json")
    ids = {u["id"] for u in users}
    chan = _load(z, "channels.json")[0]
    assert set(chan["members"]) == ids and chan["creator"] in ids
    msgs = _load(z, f"{chan['name']}/2022-01-01.json")
    for m in msgs:
        assert m["user"] in ids
        for mention in re.findall(r"<@([A-Z0-9]+)>", m["text"]):
            assert mention in ids
        assert m["reactions"][0]["users"][0] == m["user"]
        assert m["reactions"][0]["name"] == "thumbsup"
        assert m["blocks"][0]["elements"][0]["elements"][0]["type"] in ("user", "text")
    tss = {m["ts"] for m in msgs}
    threaded = [m for m in msgs if "thread_ts" in m]
    assert threaded and all(m["thread_ts"] in tss for m in threaded)
    assert len(tss) == len(msgs)  # timestamps still unique message IDs
    assert f"<#{chan['id']}|{chan['name']}>" in msgs[0]["text"]
    dm_id = _load(z, "dms.json")[0]["id"]
    assert any(n.startswith(dm_id + "/") for n in z.namelist())


def test_dates_reduced_to_year(synth, harvested):
    people, ws, meta, ctx, z = _scrub(synth, harvested)
    import datetime as dt
    for name in z.namelist():
        assert not re.search(r"\d{4}-(?!01-01)\d\d-\d\d", name), name
        for m in re.finditer(r'"(ts|thread_ts|created|updated)": "?(\d{9,10})', z.read(name).decode()):
            when = dt.datetime.fromtimestamp(int(m.group(2)), dt.timezone.utc)
            assert (when.month, when.day) == (1, 1), (name, m.group(0))


def test_group_dm_names_pseudonymized(synth, harvested):
    people, ws, meta, ctx, z = _scrub(synth, harvested)
    name = _load(z, "mpims.json")[0]["name"]
    assert re.fullmatch(r"mpdm-id_\d{4}--id_\d{4}-1", name)
    assert any(n.startswith(name + "/") for n in z.namelist())


def test_slack_export_in_a_subfolder(synth, harvested):
    people = [synth.person() for _ in range(4)]
    data, meta = SlackWorkspace(synth, people).zip_bytes(days=1, root="Acme Slack export/")
    out = zipfile.ZipFile(io.BytesIO(scrub_zip_bytes(data, harvested(people), "s.zip")))
    assert any(n.endswith("users.json") for n in out.namelist())
