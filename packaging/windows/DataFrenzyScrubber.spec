# PyInstaller recipe for the Windows desktop app.
#   pyinstaller packaging/windows/DataFrenzyScrubber.spec --noconfirm
# Produces dist/DataFrenzyScrubber/ (one folder: the .exe plus _internal/).
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parents[1]
import sys  # noqa: E402
sys.path.insert(0, str(ROOT / "src"))  # so collect_all() can find the scrubber package's own files

datas, binaries, hiddenimports = [], [], []
for pkg in ("scrubber", "spacy", "thinc", "en_core_web_lg", "presidio_analyzer", "presidio_anonymizer",
            "presidio_image_redactor", "pymupdf", "cv2", "pydicom", "gdcm", "pillow_heif", "webview",
            "tldextract", "phonenumbers", "spacy_legacy", "spacy_loggers", "srsly", "blis", "cymem",
            "preshed", "murmurhash", "catalogue", "confection", "weasel", "langcodes", "pypff", "openpyxl",
            "docx", "lxml", "bs4", "uvicorn", "fastapi", "starlette", "jinja2", "multipart"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

datas += [(str(ROOT / "data"), "data"), (str(ROOT / "LICENSE"), "."), (str(ROOT / "THIRD_PARTY_LICENSES.md"), ".")]
# AGPL: the complete source code ships inside every download (made by `git archive` before the build)
if (ROOT / "dist" / "source.zip").exists():
    datas += [(str(ROOT / "dist" / "source.zip"), ".")]

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "IPython", "pytest", "faker"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DataFrenzyScrubber", console=False,
          version=None, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="DataFrenzyScrubber")
