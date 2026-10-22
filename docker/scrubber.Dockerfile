# syntax=docker/dockerfile:1.7
# Data Frenzy Local Scrubber - processing image.
# Everything (packages, spaCy model, reference data) is installed here at build
# time. At runtime the container has no network at all (network_mode: none).

FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    SCRUBBER_IN_CONTAINER=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/cache

# pst-utils: readpst (Outlook .pst -> mbox). tesseract: OCR for images and
# scanned PDFs. libgl1/libglib2.0: OpenCV (face detection).
RUN apt-get update \
 && apt-get install -y --no-install-recommends pst-utils tesseract-ocr tesseract-ocr-eng libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/scrubber
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl \
 && python -c "import spacy; spacy.load('en_core_web_lg')"

# Optional local speech-to-text for call recordings (docker compose build --build-arg WITH_WHISPER=1).
# The model is downloaded here, at build time, and never at runtime.
ARG WITH_WHISPER=0
ARG WHISPER_MODEL=base.en
RUN if [ "$WITH_WHISPER" = "1" ]; then \
      pip install faster-whisper==1.2.1 \
      && HF_HUB_OFFLINE=0 python -c "from faster_whisper.utils import download_model; download_model('$WHISPER_MODEL', output_dir='/opt/scrubber/models/whisper')" ; \
    fi
ENV SCRUBBER_WHISPER_DIR=/opt/scrubber/models/whisper

COPY data/ /opt/scrubber/data/
COPY src/ /opt/scrubber/src/
ENV PYTHONPATH=/opt/scrubber/src SCRUBBER_DATA_DIR=/opt/scrubber/data
RUN pip freeze > /opt/scrubber/pip-freeze.txt \
 && useradd --uid 1000 --create-home scrubber \
 && mkdir -p /input /output /work /state /config \
 && chown scrubber:scrubber /output /work /state

USER scrubber
ENTRYPOINT ["python", "-m", "scrubber"]
CMD ["run"]

# Test image: same runtime plus pytest/Faker and the test suite.
FROM runtime AS test
USER root
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
# espeak-ng: synthetic speech for the optional Whisper test (test image only)
RUN apt-get update && apt-get install -y --no-install-recommends espeak-ng && rm -rf /var/lib/apt/lists/*
COPY tests/ /opt/scrubber/tests/
COPY docker-compose.yml /opt/scrubber/docker-compose.yml
USER scrubber
ENTRYPOINT ["python", "-m", "pytest", "-p", "no:cacheprovider"]
CMD ["/opt/scrubber/tests", "-q"]
