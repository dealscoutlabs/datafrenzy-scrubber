# Technical notes (Phase 1)

## Layout

```
docker-compose.yml            scrubber (network_mode: none, read-only root) + tests profile
docker/scrubber.Dockerfile    python:3.11-slim (pinned digest) + Presidio + spaCy en_core_web_lg + readpst
config/scrubber.yaml          user overrides (merged over src/scrubber/default_config.yaml)
data/                         bundled reference data (HHS ZIP3 list, Census name lists) - see data/SOURCES.md
src/scrubber/
  cli.py, pipeline.py         run / status / review / check-network; scan -> pst -> harvest -> jobs -> verify -> release
  netguard.py                 interface check + in-process socket block + offline library config
  keys.py, state.py           local HMAC secret; SQLite ledger, shards, pseudonym table, quarantine, review
  identifiers.py              the 18 Safe Harbor categories -> internal entity types
  detect/engine.py            Presidio AnalyzerEngine + post-processing (thresholds, allowlists, overlaps)
  detect/recognizers.py       custom recognizers (labeled IDs, MBI, DEA, VIN, addresses, dates, ages, names)
  detect/harvest.py           names from email headers + name columns -> exact-match list
  transform/                  pseudonym tokens (HMAC), generalization (ZIP3/year/90+), Presidio anonymizer
  formats/                    email_ (eml + MIME parts), mbox (sharded), tabular (csv/xlsx), text
tests/                        Faker-only fixtures; run with scripts/test.sh or `docker compose run --rm tests`
```

## Presidio

Presidio comes from PyPI (`presidio-analyzer`/`presidio-anonymizer` 2.2.364), the packages published by github.com/data-privacy-stack/presidio. The project's ghcr.io images aren't used as the base for two reasons. They are REST microservices, and with `network_mode: none` separate containers can't talk to each other. They also ship Python 3.13, while this project targets 3.11. presidio-image-redactor gets added in Phase 3.

Built-in recognizers kept: email, phone, URL, IP, credit card, IBAN, crypto, ITIN, medical license (DEA), MAC, spaCy NER. The SSN, bank, driver-license, passport and date built-ins are replaced by stricter custom recognizers.

## No-network guarantees

1. Compose runs the scrubber and tests with `network_mode: none`. `tests/test_network.py` asserts this and that only `lo` exists.
2. On startup the CLI refuses to run if any non-loopback interface is visible.
3. `netguard.install()` blocks internet socket connect, sendto and DNS in-process.
4. `tldextract` (used by Presidio's email recognizer) normally downloads the public suffix list on first use. It is pointed at its bundled snapshot instead. This was found because the download attempt failed inside the network-less container.

## Pseudonyms

The key is `HMAC-SHA256(secret, entity_type + normalized value)`, and only HMACs are stored in `/state/state.db`. Token numbers are assigned in first-seen order. Names are normalized (honorifics, suffixes and middle initials dropped; "Last, First" reordered). A lone first or last name that matches exactly one harvested full name gets that person's token. Message-IDs and Content-IDs become HMAC-derived opaque IDs, which keeps reply threading intact.

## Verification gate

Each output is re-opened and checked for structure (MIME tree, CSV row and field counts, XLSX sheets and dimensions, mbox message count). Detection then runs again on its text. Any finding at or above `verification.residual_threshold` (other than our own tokens) sends the file to the review queue in `/work/staging`, and nothing goes to `/output` until it is approved. Review reasons hold only types and scores, never the matched text, so the summary report has no PHI.

## Scale

- mbox files larger than `mbox.shard_mb` (256 MB) are split at `From ` lines into byte ranges. The ranges are processed in parallel and checkpointed every N messages.
- CSV is streamed in 500-row batches with byte-offset checkpoints. Each file is single-process; many files run in parallel.
- Workers are forked after the spaCy model loads, so the model memory is shared.
- XLSX is loaded whole (openpyxl needs that to keep formatting), so very large workbooks need RAM.

## Tests and recall

`scripts/test.sh` runs 136 tests in the network-less container. Recall on synthetic free text (N=200 per type, `tests/test_recall.py`):

| type | recall | type | recall |
|---|---|---|---|
| names | 0.990 | address | 0.980 |
| email | 1.000 | city | 0.980 |
| phone | 1.000 | ZIP | 0.980 |
| dates | 1.000 | URL | 1.000 |
| SSN | 1.000 | IP | 1.000 |
| MRN | 1.000 | vehicle | 0.990 |
| health plan | 1.000 | device | 1.000 |
| account | 1.000 | biometric | 1.000 |
| license | 1.000 | age 90+ | 1.000 |
| other IDs | 1.000 | | |

The templates were written alongside the recognizers, so these figures are optimistic. On a held-out probe with differently phrased text (chat style, lowercase, unlabeled), recall was 0.981 for names and 1.000 for IDs, phones and dates. Typical misses: a lone surname that is also a common word ("loop in White") and lowercase names made of everyday words ("amber best"). Real data will do worse than synthetic data. That's why the deny list, name harvesting and the review gate exist.

## Known Phase 1 limitations

- HTML email text nodes are scrubbed one at a time. A name split across tags relies on the verification pass, which reads the joined text.
- XLSX: embedded images and pivot caches are removed (and listed). Charts are dropped by openpyxl. Rich text inside a single cell is flattened to plain text. `.xlsm` and `.xls` are quarantined.
- Email attachments in Phase 2/3 formats (PDF, DOCX, images) are replaced with a short note part and listed in the report.
- `.pst` output is a folder of `.mbox` files (`archive.pst.mbox/<folder>.mbox`). There is no free writer for `.pst`.
- Organization names made of ordinary surnames ("Gray and Duncan") are tokenized as PERSON, not ORG. They are still removed, but counted under the wrong category.
- Context-risk checks (rare diagnosis + place, unique job titles ...), the manifest and the HTML/PDF audit report are Phase 4. `output/reports/run-summary.json` is the Phase 1 stand-in.
