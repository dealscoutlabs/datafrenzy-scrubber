# Data Frenzy Local Scrubber

This program removes patient and personal information from your business files **on your own computer**, so the files can be reviewed and then sold as de-identified data.

- **Nothing leaves your computer.** The scrubber runs with networking switched off. It can't send data anywhere, even by accident.
- **Your files stay the same kind of file.** An email is still an email, and a spreadsheet is still a spreadsheet. Names, phone numbers, dates and other identifiers are replaced with stand-ins like `PERSON_0412` or `PHONE_0007`.
- **The same person always gets the same stand-in.** "Maria Gonzalez" is `PERSON_0412` in every email and spreadsheet, so conversations still make sense.
- It follows the **HIPAA Safe Harbor** method, which covers all 18 types of identifiers.

> **Works with:**
> - email (`.eml`, `.mbox`, Outlook `.pst`)
> - spreadsheets (`.csv`, `.xlsx`)
> - Slack workspace exports (`.zip`)
> - Word documents (`.docx`) and PDFs, including scanned PDFs
> - photos and images (`.jpg`, `.png`, `.tif`, `.heic` and more)
> - medical images (`.dcm` DICOM)
> - JSON files and plain text
>
> Voice recordings are never exported. You review everything in your web browser, and the scrubber writes a data manifest and a de-identification report for your compliance officer. Files the scrubber can't handle are **never exported**. They are listed in the report instead.

---

## 1. Install Docker (one time)

The scrubber runs inside **Docker**, a free program that keeps it sealed off from the internet and the rest of your computer.

1. Go to <https://www.docker.com/products/docker-desktop/> and download **Docker Desktop** for your computer (Mac or Windows).
2. Install it and open it. Wait until it says Docker is running.

> Docker Desktop is free for businesses with fewer than 250 employees **and** under $10 million in yearly revenue. Larger businesses need a paid Docker subscription. Free alternatives work the same way: **Rancher Desktop** (Mac/Windows) or **Colima** (Mac).

## 2. Get the scrubber (one time)

Put the `datafrenzy-scrubber` folder somewhere easy to find, such as your Documents folder.

The first run downloads about 2 GB of tools and language models. **This is the only time internet is used**, and none of your data is involved. After that, the scrubber works with the internet unplugged.

## 3. Copy your files in

Inside the `datafrenzy-scrubber` folder, create a folder named **`input`** and copy your files into it. Subfolders are fine; they are kept as they are.

```
datafrenzy-scrubber/
  input/
    Email/            <- .mbox / .eml / .pst files
    Spreadsheets/     <- .csv / .xlsx files
    Slack/            <- the Slack export .zip, exactly as Slack gave it to you
    Documents/        <- .docx / .pdf / .txt files
```

**Slack:** a workspace owner or admin downloads the export from Slack (*Workspace settings → Import/Export Data → Export*) and copies the `.zip` in **without unzipping it**. In the scrubbed export, each channel's daily files are combined into one file per year (for example `general/2022-01-01.json`), because file names showing the day and month are themselves dates that must be removed. All message times are moved to January 1 of their year, and threads and replies stay linked.

**Pictures and scans:**
- Text in photos, scans and scanned PDFs is read on your computer, and names, numbers and other identifiers are blacked out.
- Faces are blurred beyond recognition.
- Camera details, dates and GPS location are removed from photos.
- Scanned PDFs are rebuilt from the cleaned page images.
- Blurry, faint or handwritten text that the reader isn't sure about is **held for review** instead of exported.
- Pictures inside spreadsheets are removed. Pictures inside Word files, PDFs and emails are cleaned the same way as photos.

**Medical images (DICOM):** patient name, ID, birth date, dates, institution and similar fields are scrubbed, and text burned into the image is blacked out. Studies and series stay linked to each other.

**Voicemails and call recordings** are never exported, because a voice is a biometric identifier. If you want the *words* of recordings, the scrubber can type them out on your computer and export only the scrubbed text. This needs a larger install (about 150 MB more), so it is off by default. To turn it on:
1. Run `docker compose build --build-arg WITH_WHISPER=1` once.
2. Add `audio: {transcribe: true}` to `config/scrubber.yaml`.

**Outlook users:** copy your `.pst` file in directly. If it won't read (some are encrypted or very old), open Outlook on Windows and use *File → Open & Export → Import/Export → Export to a file → Outlook Data File (.pst)* without a password, then copy that file in. Outlook for Mac can't make `.pst` files. Instead, drag the folders into Apple Mail and use *Mailbox → Export Mailbox*, then copy the exported `.mbox` folders in. Single `.msg` files aren't supported, so export the whole folder instead.

## 4. Run it

Open **Terminal** (Mac) or **PowerShell** (Windows) in the `datafrenzy-scrubber` folder and type:

```bash
docker compose up
```

You will see progress messages. When it says **Finished**, open **<http://localhost:8080>** in your web browser. That's the review screen. Leave the Terminal window open while you review. To stop, press `Ctrl+C` in Terminal.

It's safe to run again at any time. Files that are already done are skipped. If your computer shuts down halfway, run the same command again and it continues where it stopped.

## 5. Look at the results

Everything is written to the **`output`** folder:

| File or folder | What's in it |
|---|---|
| `output/scrubbed/` | Your files with identifiers removed, in the same folders and formats as `input`. Folder and file names are scrubbed too. |
| `output/manifest.csv` (and `.json`) | What the export contains, per source: number of files, size, records or messages, and the years covered. Data Frenzy uses this to get buyer quotes. |
| `output/reports/deidentification-report.pdf` (and `.html`) | The de-identification report for your compliance officer. It covers the method, the 18-identifier checklist with counts, the double-check results, what was held back, review decisions, settings and software versions, plus a signature section. |

## 6. Review in your browser

The review screen at <http://localhost:8080> works only on this computer. It shows:

- **Home:** how many identifiers of each type were removed, and the scrubber's status. The **Re-run** button is here.
- **Review queue:** files the automatic double-check held back. Reasons include a possible leftover name, low-quality OCR, possible handwriting, or a context risk such as "our only pediatric surgeon", a rare disease plus a town, or "Dr. Smith's daughter". Each file shows what was flagged (highlighted) and the original next to the scrubbed version. Type your name and choose **Approve and export** or **Reject**. Decisions go into the report.
- **Samples:** a random set of passages, original next to scrubbed, so you can spot-check the work.
- **Report a missed identifier:** type the missed text (for example a nickname or a clinic name), pick its type, and add it to the deny list. Then press **Re-run** on the home page.
- **Not exported:** everything that was held back, and why.

Originals appear only on this screen, on your computer. They are never exported.

## 7. Your own lists (optional)

Names and words added on the review screen are saved in `config/review-additions.yaml`. For anything else, such as your own ID formats, extra job titles or town names to flag, or companies to keep, edit **`config/scrubber.yaml`**. Then run `docker compose up` again.

## 8. Export

**Only share the `output` folder** (scrubbed files, manifest and report). Never share these folders:

- `state/`: holds the secret key that keeps the stand-ins consistent, and the lookup table. With these, someone could link stand-ins back together. Keep them, but keep them private.
- `work/`: temporary copies, including files waiting for review (these still contain original data).
- `input/`: your original files.

---

## What is removed (HIPAA Safe Harbor)

1. Names of people, including relatives, household members and employers
2. Street addresses, cities, counties and ZIP codes. ZIP codes keep only their first 3 digits, or `000` for small areas.
3. All parts of dates except the year (birth, visit, admission, discharge, death...). Ages over 89 become "90+".
4. Phone numbers
5. Fax numbers
6. Email addresses
7. Social Security numbers
8. Medical record numbers
9. Health plan numbers (Medicare, Medicaid, insurance member IDs)
10. Account numbers
11. Certificate and license numbers (including NPI and DEA)
12. Vehicle IDs and license plates
13. Device IDs and serial numbers
14. Web addresses (URLs)
15. IP addresses
16. Biometric identifiers. Voice recordings (voicemails, call recordings, videos) are **never exported**; optionally, scrubbed transcripts are.
17. Face photos. Faces in photos, scans, documents and medical images are blurred beyond recognition.
18. Any other unique number or code (claim and case numbers, usernames, social media handles, long ID codes)

Large public companies such as UnitedHealthcare, Microsoft and Medicare are left in. You can change that list in `config/scrubber.yaml`.

## Troubleshooting

| Problem | What to do |
|---|---|
| `docker: command not found` or `Cannot connect to the Docker daemon` | Open Docker Desktop and wait until it's running, then try again. |
| `unknown command: docker compose` | Your Docker is missing the Compose add-on. Docker Desktop includes it. On Mac with Homebrew, run `brew install docker-compose`. |
| "Refusing to run: network interfaces present" | You started the scrubber some other way. Always use `docker compose up`, which switches the network off. |
| A file shows as "could not be opened (encrypted or damaged)" | Remove the password in the program that made it, save a copy, put the copy in `input`, and run again. |
| The review screen at localhost:8080 doesn't open | Check that Terminal still shows the scrubber running (`docker compose up`). If another program uses port 8080, change `127.0.0.1:8080:8080` in `docker-compose.yml` to e.g. `127.0.0.1:8090:8080` and open localhost:8090. |
| Lots of files are held for "context risk" | These are phrases like "the CEO" or "our only surgeon". Review them, or switch a rule off in `config/scrubber.yaml` under `context_risk: disabled_rules`. |
| A PDF shows as "text layer is unreadable" in the review list | The PDF uses an unusual font that hides its text. Open it, check it by eye, and approve or reject it. |

For technical details, see [docs/TECHNICAL.md](docs/TECHNICAL.md).
