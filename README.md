# Data Frenzy Local Scrubber

This program removes patient and personal information from your business files **on your own computer**, so the files can be reviewed and then sold as de-identified data.

- **Nothing leaves your computer.** The scrubber runs with networking switched off. It can't send data anywhere, even by accident.
- **Your files stay the same kind of file.** An email is still an email, and a spreadsheet is still a spreadsheet. Names, phone numbers, dates and other identifiers are replaced with stand-ins like `PERSON_0412` or `PHONE_0007`.
- **The same person always gets the same stand-in.** "Maria Gonzalez" is `PERSON_0412` in every email and spreadsheet, so conversations still make sense.
- It follows the **HIPAA Safe Harbor** method, which covers all 18 types of identifiers.

> **Status: Phase 2.** Works today: email (`.eml`, `.mbox`, Outlook `.pst`), spreadsheets (`.csv`, `.xlsx`), Slack workspace exports (`.zip`), Word documents (`.docx`), PDFs that contain selectable text, JSON files and plain text. Coming later: photos, scanned documents and X-rays (Phase 3), and the review web page and full audit report (Phase 4). Files the scrubber can't handle yet are **never exported**. They are listed in the report instead.

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

**PDFs:** PDFs where you can select the text are scrubbed now. Scanned PDFs (pictures of pages) are held back until Phase 3. Pictures inside Word files and PDFs are greyed out until Phase 3 adds face blurring.

**Outlook users:** copy your `.pst` file in directly. If it won't read (some are encrypted or very old), open Outlook on Windows and use *File → Open & Export → Import/Export → Export to a file → Outlook Data File (.pst)* without a password, then copy that file in. Outlook for Mac can't make `.pst` files. Instead, drag the folders into Apple Mail and use *Mailbox → Export Mailbox*, then copy the exported `.mbox` folders in. Single `.msg` files aren't supported, so export the whole folder instead.

## 4. Run it

Open **Terminal** (Mac) or **PowerShell** (Windows) in the `datafrenzy-scrubber` folder and type:

```bash
docker compose up
```

You will see progress messages. When it says **Finished**, it shows how many files were exported, how many are waiting for review, and how many were held back.

It's safe to run again at any time. Files that are already done are skipped. If your computer shuts down halfway, run the same command again and it continues where it stopped.

## 5. Look at the results

Everything is written to the **`output`** folder:

| Folder | What's in it |
|---|---|
| `output/scrubbed/` | Your files with identifiers removed, in the same folders and formats as `input`. Folder and file names are scrubbed too. |
| `output/reports/run-summary.json` | Counts of what was removed for each of the 18 identifier types, which files were held back and why, and which files are waiting for review. |

Open a few scrubbed files to check them. Emails open in Apple Mail, Outlook or Thunderbird, and spreadsheets open in Excel.

## 6. Review files that need a human look

Some files are **held for review** instead of exported. This happens when the automatic double-check finds something that might still identify a person. They are not in `output/scrubbed/` until you approve them.

To see them:

```bash
docker compose run --rm scrubber review list
```

If a file is fine (or once you've added the missed name to `config/scrubber.yaml` and re-run), approve it:

```bash
docker compose run --rm scrubber review approve "path/shown/in/the/list" --by "Your Name"
```

To keep a file out permanently, use `review reject` the same way. (Phase 4 replaces these commands with a simple web page.)

## 7. Teach it about missed names (optional)

If you spot a name or ID that wasn't removed, add it to **`config/scrubber.yaml`** under `deny_list`, save, and run `docker compose up` again. Only affected files are redone.

## 8. Export

**Only share the `output` folder.** Never share these folders:

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
16. Biometric identifiers. Voice recordings (voicemails, call recordings, videos) are **never exported**.
17. Face photos. Image files are **never exported**, and pictures inside Word files and PDFs are greyed out (until Phase 3).
18. Any other unique number or code (claim and case numbers, usernames, social media handles, long ID codes)

Large public companies such as UnitedHealthcare, Microsoft and Medicare are left in. You can change that list in `config/scrubber.yaml`.

## Troubleshooting

| Problem | What to do |
|---|---|
| `docker: command not found` or `Cannot connect to the Docker daemon` | Open Docker Desktop and wait until it's running, then try again. |
| `unknown command: docker compose` | Your Docker is missing the Compose add-on. Docker Desktop includes it. On Mac with Homebrew, run `brew install docker-compose`. |
| "Refusing to run: network interfaces present" | You started the scrubber some other way. Always use `docker compose up`, which switches the network off. |
| A file shows as "could not be opened (encrypted or damaged)" | Remove the password in the program that made it, save a copy, put the copy in `input`, and run again. |
| A PDF shows as "text layer is unreadable" in the review list | The PDF uses an unusual font that hides its text. Open it, check it by eye, and approve or reject it. |

For technical details, see [docs/TECHNICAL.md](docs/TECHNICAL.md).
