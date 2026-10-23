# Performance

Measured on 2026-10-22 on a 2019 Intel Mac running Docker through Colima. The container had **4 CPU cores and 8 GB of RAM**, with networking off. The data was synthetic (Faker identities) and each category ran on its own from a fresh state. Times include everything: loading the language model, scanning, collecting names, scrubbing, verifying every output and writing the reports.

## Results

| Data | Size | Time | Speed | Held for review |
|---|---|---|---|---|
| Plain-text notes (200 files) | 4.8 MB | 116 s | 41 KB/s | 4 files |
| Word + PDF (200 files) | 7.6 MB | 88 s | 86 KB/s | 13 files |
| Slack export (1 zip, 600 messages) | 1.5 MB | 25 s | 60 KB/s | 1 file |
| CSV + XLSX (1 × 48 MB CSV, 5 workbooks) | 48 MB | 17 min | 48 KB/s | 0 |
| Email (2 mailboxes, about 9,000 messages) | 98 MB | 37 min | 45 KB/s | see note |
| Images, scanned PDFs, DICOM (120 photos/notes, 20 three-page scans, 20 DICOM) | 386 MB | 86 s | about 2 files/s | 1 file |

**Note on email:** in this run any flagged message held its whole mailbox. That has since been changed, so now only the flagged message is held, as its own review item, and the rest of the mailbox is exported.

## What sets the speed

- **Text (email bodies, notes, CSV free-text columns, Word, PDF text, Slack):** roughly **10–12 KB/s per CPU core**, counting both the scrub and the verification pass. About 75% of that time is spaCy's `en_core_web_lg` entity recognizer, which already runs with only the parts it needs. Structured spreadsheet columns (name, phone, MRN …) are tokenized without the language model and verified by format, so they cost little.
- **Images and scans:** Tesseract OCR, run up to three times to scrub and once more to verify. On their own: about 0.5 s per photo, 0.4 s per DICOM image, 2–3 s per scanned page. Time scales with pixels.
- **Workers:** one per CPU core, sharing the model in memory. A 16-core machine is roughly 4× faster than the one above, if it has enough RAM (about 1.5 GB per worker).

## Rough planning numbers (4-core office PC)

| Seller's data | Rough time |
|---|---|
| 10 GB of email (typical small practice, text-heavy) | about 2.5 days |
| 1 GB of CRM/spreadsheet exports | about 6 hours |
| 10,000 scanned pages | about 2–3 hours |

Email archives counted in gigabytes are mostly attachments. Attachments that are images get OCR; the rest (PDF, Word, spreadsheets) run at the text speeds above. Expect a terabyte-scale archive to need a multi-core machine and several weeks, or to be split across several machines (the tool is resumable, so one machine can also run over many nights).

## Changes made after the first measurement

| Change | Effect |
|---|---|
| Call Presidio's recognizers directly; skip its context booster and duplicate removal (both quadratic, and the duplicate removal also dropped precise city matches) | Detection about 2.9× faster on a 21 KB note; a missed-city bug fixed |
| Load only spaCy's entity recognizer (no tagger, parser or lemmatizer) | Same entities, about 34% faster model step |
| North American phone matcher only; own URL patterns instead of Presidio's | Removed two hotspots |
| Verify spreadsheet cells one by one, and skip cells that hold only tokens or short numbers | CSV verification much cheaper; false "company name" flags on words like "Active" gone |
| Firm-name, last-name-first and town-next-to-ZIP patterns | Real leaks found by verification now caught in the first pass |
| Per-message review holds for mailboxes | One flagged email no longer holds back a whole mailbox |
| One thread per worker for Tesseract and OpenCV (`OMP_THREAD_LIMIT=1`) | Image run 4.8 hours → 86 seconds (nested threads were fighting for 4 cores) |
| Images read and redacted up to 3 rounds before verification | OCR that garbled a number on the first read no longer leaves it visible (6 SSN leaks in the first image run were caught by verification; all fixed) |

## How to reproduce

```bash
# 1. generate the synthetic corpus (scale 1 = about 900 MB)
docker run --rm --network none -v "$PWD/bench:/bench" -v "$PWD/tests:/opt/scrubber/tests:ro" \
  -v "$PWD/tools/bench:/tools:ro" --entrypoint python datafrenzy/local-scrubber-tests:0.1.0 /tools/gen.py /bench/input 1
# 2. time each category
tools/bench/time.sh bench/input bench/results
```
