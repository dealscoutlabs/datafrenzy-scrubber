# Third-party software

Data Frenzy Local Scrubber is licensed under the **GNU Affero General Public
License v3.0** (see `LICENSE`). It is AGPL because it includes PyMuPDF, which
is AGPL-3.0. All other components use licenses that are compatible with it.

## Python packages (in the image)

| Package | Version | License |
|---|---|---|
| PyMuPDF | 1.28.2 | AGPL-3.0 (or Artifex commercial license) |
| presidio-analyzer | 2.2.364 | MIT |
| presidio-anonymizer | 2.2.364 | MIT |
| presidio-image-redactor | 0.0.60 | MIT |
| spaCy | 3.8 | MIT |
| en_core_web_lg (spaCy model) | 3.8.0 | MIT |
| python-docx | 1.2.0 | MIT |
| openpyxl | 3.1.5 | MIT |
| pydicom | 3.0.2 | MIT |
| python-gdcm | 3.2.6 | Apache-2.0 |
| opencv-python | 4.14.0.94 | Apache-2.0 |
| pytesseract | 0.3.13 | Apache-2.0 |
| Pillow | 12.3.0 | MIT-CMU |
| pillow-heif | 1.8.0 | BSD-3-Clause |
| beautifulsoup4 | 4.12.3 | MIT |
| lxml | 6.x | BSD-3-Clause |
| phonenumbers | 9.x | Apache-2.0 |
| python-dateutil | 2.9.0 | Apache-2.0 / BSD |
| PyYAML | 6.x | MIT |
| FastAPI | 0.142.2 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| Jinja2 | 3.1.6 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| faster-whisper (optional, `WITH_WHISPER=1`) | 1.2.1 | MIT |

The exact installed versions are recorded in the image at `/opt/scrubber/pip-freeze.txt`.

## Programs (Debian packages in the image)

| Program | License | How it is used |
|---|---|---|
| Tesseract OCR | Apache-2.0 | OCR for images and scanned PDFs |
| readpst (pst-utils / libpst) | GPL-2.0-or-later | run as a separate program to convert Outlook `.pst` to mbox |
| espeak-ng | GPL-3.0-or-later | test image only (synthetic speech for the Whisper test) |

## Models and data

| Item | License |
|---|---|
| OpenCV Zoo YuNet face detector (`data/models/`) | MIT |
| Whisper model (optional, downloaded at build time) | MIT |
| US Census name lists, HHS ZIP3 list (`data/`) | Public domain (US Government) |
