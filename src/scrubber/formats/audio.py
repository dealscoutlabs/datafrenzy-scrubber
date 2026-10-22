"""Audio and video: voiceprints are biometric identifiers, so recordings are
never exported. Optionally (audio.transcribe: true, image built with
WITH_WHISPER=1) a recording is transcribed on this machine with Whisper and
only the scrubbed transcript is exported."""

from __future__ import annotations

import os
from pathlib import Path

from .base import Context, Quarantined

TRANSCRIPT_SUFFIX = ".transcript.txt"
_model = None


def whisper_dir() -> Path | None:
    d = Path(os.environ.get("SCRUBBER_WHISPER_DIR", "/opt/scrubber/models/whisper"))
    if not (d.is_dir() and any(d.iterdir())):
        return None
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return None
    return d


def transcription_enabled(cfg: dict) -> bool:
    return bool(cfg.get("audio", {}).get("transcribe")) and whisper_dir() is not None


def transcribe(path: Path) -> str:
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        d = whisper_dir()
        if d is None:
            raise Quarantined("transcription is turned on, but this install has no Whisper model "
                              "(rebuild with WITH_WHISPER=1)")
        _model = WhisperModel(str(d), device="cpu", compute_type="int8", local_files_only=True)
    segments, _info = _model.transcribe(str(path), language="en", beam_size=5)
    return "\n".join(seg.text.strip() for seg in segments if seg.text.strip())


def process_media(src: Path, dst: Path, ctx: Context) -> None:
    """dst is the transcript path. The recording itself is never written anywhere."""
    text = transcribe(src)
    scrubbed = ctx.scrubber.scrub(text, ctx.tally).text
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(f"[Transcript of a recording; the audio was not exported. Speaker voices removed.]\n\n{scrubbed}\n")
    ctx.residual(dst.read_text(), ctx.out_rel)
    ctx.tally.records += 1
