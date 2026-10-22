"""Voice recordings: never exported. Optional local Whisper transcripts."""

import shutil
import subprocess

import pytest

from scrubber.formats.audio import TRANSCRIPT_SUFFIX, whisper_dir


def test_recordings_never_exported_by_default(workspace, run_pipeline):
    (workspace.input / "voicemail.wav").write_bytes(b"RIFF" + bytes(200))
    (workspace.input / "telehealth.mp4").write_bytes(bytes(200))
    _, summary = run_pipeline(workspace)
    assert summary["files"] == {"quarantined": 2}
    assert not workspace.released.exists() or not any(workspace.released.rglob("*"))
    assert summary["identifiers"][15]["by_type"]["MEDIA_BIOMETRIC"] == 2


@pytest.mark.skipif(whisper_dir() is not None, reason="this image includes Whisper")
def test_transcription_requested_without_whisper_is_explained(workspace, run_pipeline):
    (workspace.input / "call.mp3").write_bytes(bytes(200))
    _, summary = run_pipeline(workspace, overrides={"audio": {"transcribe": True}})
    assert summary["files"] == {"quarantined": 1}
    assert "WITH_WHISPER=1" in summary["quarantined"][0]["reason"]


@pytest.mark.skipif(whisper_dir() is None or not shutil.which("espeak-ng"),
                    reason="needs an image built with WITH_WHISPER=1")
def test_whisper_transcript_scrubbed_and_audio_dropped(workspace, run_pipeline, synth):
    p = synth.person()
    wav = workspace.input / "voicemail.wav"
    speech = (f"Hi, this is {p.first} {p.last}. My date of birth is March fourteenth, nineteen fifty two. "
              f"Please call me back at two one two, five five five, zero one four three.")
    subprocess.run(["espeak-ng", "-s", "140", "-w", str(wav), speech], check=True)
    _, summary = run_pipeline(workspace, overrides={"audio": {"transcribe": True}})
    out = workspace.released / ("voicemail.wav" + TRANSCRIPT_SUFFIX)
    assert out.exists(), summary
    text = out.read_text()
    assert p.last not in text and "March" not in text and "0143" not in text.replace("-", "")
    assert not list(workspace.released.rglob("*.wav"))
