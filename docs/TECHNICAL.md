# Technical notes (Phases 1–2)

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
  formats/                    email_ (eml + MIME parts), mbox (sharded), tabular (csv/xlsx), text,
                              json_ (JSON/JSONL + Slack rules), archive (zip + Slack export), docx_, pdf,
                              dispatch (attachments / zip members / PDF attachments by type)
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

## Phase 2 formats

**JSON / Slack.** Only string values change: keys, nesting and list lengths stay the same, and a shape check verifies it for every file. The key name picks the rule for its value, reusing the spreadsheet column rules (`email`, `phone`, `dob`, `created_at`...). Identifiers stored as numbers stay numbers (`9` + the token's sequence number).

Slack-specific rules:
- User, channel, team and file IDs map to consistent opaque IDs (`U012ABC…` becomes `UZ…`). Mentions, reactions, membership and DM folders stay linked.
- Message timestamps (`ts`, `thread_ts`…) are message IDs, so each maps to a unique value on Jan 1 of its year, and threads still resolve. Other timestamps become Jan 1 exactly.
- Day files are merged into one file per channel per year.
- `mpdm-` names are rebuilt from the members' pseudonymous handles.
- Names from `users.json` are harvested, so names in messages are found even when the model misses them.

**DOCX.** Detection runs on each paragraph's full text. Replacements are mapped back onto the original `w:t` runs (`transform/scrub.py:apply_spans_to_segments`), so formatting survives and a name split across runs is still caught.

Parts covered:
- body, tables, text boxes, headers/footers, footnotes/endnotes and comments, including parts python-docx only exposes as raw XML
- tracked insertions and deletions (`w:delText`)
- authors, initials and dates on revisions and comments
- field codes and hyperlink/attached-template relationship targets
- core/app/custom properties and customXml
- images and the thumbnail are blanked; embedded xlsx/docx are scrubbed recursively

**PDF.** Uses PyMuPDF.
- Words are joined per page and identifiers are found on that text.
- The covered words are removed with `add_redact_annot` + `apply_redactions`, which deletes the glyphs from the content stream. The token is then drawn at the same spot.
- The file is rewritten (`garbage=4`), which drops old revisions.
- Verification re-extracts the text and also searches every decompressed stream for the removed strings.
- Metadata/XMP, annotations, form values, links, bookmarks, embedded files, JavaScript and thumbnails are scrubbed or removed.
- Any page more than 30% covered by images is treated as scanned, and the file is quarantined for Phase 3. Smaller images are greyed out.
- Drawn tokens are appended to the page content. Extract text by position (`get_text(sort=True)`, `pdftotext`) to read them in place.

**License note.** PyMuPDF is AGPL-3.0, or a paid Artifex commercial license. Shipping this tool to sellers with PyMuPDF inside means either releasing the tool's source under AGPL terms or buying a commercial license. If neither is acceptable, `pypdf` (BSD) plus `pdfplumber` (MIT) can replace it, but true redaction then has to be hand-built. Decide before distributing.

## Tests and recall

`scripts/test.sh` runs 161 tests in the network-less container. They include `tests/test_formats_identifiers.py`, which pushes every one of the 18-identifier cases through every format (txt, eml, csv, xlsx, json, Slack, docx with runs split mid-identifier, pdf).

Recall on synthetic free text (N=200 per type, `tests/test_recall.py`):

| type | recall | type | recall |
|---|---|---|---|
| names | 0.990 | address | 0.980 |
| email | 1.000 | city | 0.985 |
| phone | 1.000 | ZIP | 0.980 |
| dates | 1.000 | URL | 1.000 |
| SSN | 1.000 | IP | 1.000 |
| MRN | 1.000 | vehicle | 0.990 |
| health plan | 1.000 | device | 1.000 |
| account | 1.000 | biometric | 1.000 |
| license | 1.000 | age 90+ | 1.000 |
| other IDs | 1.000 | | |

The templates were written alongside the recognizers, so these figures are optimistic. On a held-out probe with differently phrased text (chat style, lowercase, unlabeled), recall was 0.981 for names and 1.000 for IDs, phones and dates. Typical misses: a lone surname that is also a common word ("loop in White") and lowercase names made of everyday words ("amber best"). Real data will do worse than synthetic data. That's why the deny list, name harvesting and the review gate exist.

## Known limitations

- HTML email text nodes are scrubbed one at a time. A name split across tags relies on the verification pass, which reads the joined text.
- XLSX: embedded images and pivot caches are removed (and listed). Charts are dropped by openpyxl. Rich text inside a single cell is flattened to plain text. `.xlsm` and `.xls` are quarantined.
- Email attachments that are images, audio/video or unsupported types are replaced with a short note part and listed in the report. PDF, DOCX, JSON, zip, CSV and XLSX attachments are scrubbed in place.
- A lone surname that is also an ordinary word, in a file name or a short label ("West_contacts.csv"), is only caught if that name was harvested from headers, name columns or Slack `users.json`.
- PDF text drawn as vector outlines (no real characters) can't be read, so it can't be redacted. Phase 3's OCR pass will cover it.
- Plain `.json` files load fully into memory (`json.max_mb`, 1 GB default). Larger exports should be JSON Lines, which is streamed.
- `.pst` output is a folder of `.mbox` files (`archive.pst.mbox/<folder>.mbox`). There is no free writer for `.pst`.
- Organization names made of ordinary surnames ("Gray and Duncan") are tokenized as PERSON, not ORG. They are still removed, but counted under the wrong category.
- Context-risk checks (rare diagnosis + place, unique job titles ...), the manifest and the HTML/PDF audit report are Phase 4. `output/reports/run-summary.json` is the Phase 1 stand-in.
