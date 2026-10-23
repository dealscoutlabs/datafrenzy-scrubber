# Pilot runbook (one friendly seller)

**Goal:** find out how the scrubber does on *real* data before anyone sells anything. All the recall numbers so far come from synthetic data. The pilot measures what the seller's own data looks like after scrubbing, and how much human review it needs.

**Data never leaves the seller's computer during the pilot.** Data Frenzy receives only the manifest and the de-identification report (`output/manifest.csv`, `output/reports/`). Those hold counts and scrubbed file names, never data.

## 1. Before you start

- **Computer:** 4+ CPU cores, 16 GB RAM recommended (8 GB minimum), and free disk space of about **3× the data size** (original + scrubbed + working copies).
- **Data:** pick a slice the seller knows well, about 5–20 GB: one or two mailboxes, a CRM export, a folder of documents, and some scans or photos if they have them. Choose a slice someone can judge ("would a stranger recognize anyone here?").
- **People:** the person running it (often the office manager) and the person who signs off (compliance officer or practice owner).

## 2. Run it

1. Follow README steps 1–4 (`docker compose up`).
2. Write down: start time, end time, the machine (cores, RAM) and the input size.
3. When it says **Finished**, open <http://localhost:8080>.

## 3. Measure (about 2 hours of reviewer time)

Record the answers in the feedback form below.

1. **Spot check of 200 passages.** Open **Samples** and reload it 10 times (20 passages each). For every passage, check whether anything identifying is left in the *scrubbed* column. Count misses by type (name, date, phone, ID number, address, other). Report each miss with **Report a missed identifier**.
2. **File check of 20 files.** Open 20 random files from `output/scrubbed/` in their normal programs (Outlook or Apple Mail, Excel, Word, a PDF reader, an image viewer). Note anything identifying and anything broken: a file that won't open, lost formatting, or unreadable text.
3. **Review queue.** How many files were held? How long did each decision take? Were the reasons sensible, or mostly noise (e.g. "the owner" in ordinary business text)?
4. **Over-redaction.** In the 200 passages, count words that were removed but weren't identifiers (e.g. a drug name turned into PERSON_0007). Too much over-redaction lowers the data's value.
5. **Re-run.** After reporting misses, press **Re-run** and check that the reported terms are now gone.

## 4. Decide

Rough bar for moving on to a full run:

- **0 misses** of names, SSNs, MRNs, member/account IDs, phone numbers or emails in the 200-passage check (after adding reported terms and re-running).
- No broken files in the 20-file check.
- A review queue the seller can actually work through (rule of thumb: under 5% of files). If context-risk flags are mostly noise, adjust `context_risk.disabled_rules` or the lists in `config/scrubber.yaml`.
- The compliance officer has read the report and is willing to sign its attestation.

If the bar isn't met, send Data Frenzy the *types and counts* of misses (never the text). The detectors get tuned and the pilot is repeated.

## 5. Feedback form

| Item | Answer |
|---|---|
| Input size / file count | |
| Machine (cores, RAM, OS) | |
| Run time (start → Finished) | |
| Files exported / held for review / not exported | |
| Misses in 200 passages, by type | |
| Over-redacted words in 200 passages | |
| Broken files in the 20-file check | |
| Review queue: files held, % that were real issues, minutes per decision | |
| Anything confusing in the README or review screen | |
