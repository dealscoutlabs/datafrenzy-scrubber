# PyInstaller recipe for the Mac app (trial build: unsigned / ad-hoc signed).
#   .venv-mac/bin/pyinstaller packaging/macos/DataFrenzyScrubber.spec --noconfirm
# Produces dist/Data Frenzy Scrubber.app. OCR uses Homebrew's Tesseract (brew install tesseract).
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parents[1]
import sys  # noqa: E402
sys.path.insert(0, str(ROOT / "src"))  # so collect_all() can find the scrubber package's own files

datas, binaries, hiddenimports = [], [], []
for pkg in ("scrubber", "spacy", "thinc", "en_core_web_lg", "presidio_analyzer", "presidio_anonymizer",
            "presidio_image_redactor", "pymupdf", "cv2", "pydicom", "gdcm", "pillow_heif", "webview",
            "tldextract", "phonenumbers", "spacy_legacy", "spacy_loggers", "srsly", "blis", "cymem",
            "preshed", "murmurhash", "catalogue", "confection", "weasel", "langcodes", "openpyxl",
            "docx", "lxml", "bs4", "uvicorn", "fastapi", "starlette", "jinja2", "multipart"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

datas += [(str(ROOT / "data"), "data"), (str(ROOT / "LICENSE"), "."), (str(ROOT / "THIRD_PARTY_LICENSES.md"), ".")]

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas, binaries=binaries, hiddenimports=hiddenimports,
    excludes=["tkinter", "IPython", "pytest", "faker"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DataFrenzyScrubber", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="DataFrenzyScrubber")
app = BUNDLE(coll, name="Data Frenzy Scrubber.app", bundle_identifier="ai.datafrenzy.scrubber",
             info_plist={"CFBundleShortVersionString": "0.1.0", "NSHighResolutionCapable": True,
                         "LSMinimumSystemVersion": "12.0"})
