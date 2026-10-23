"""Synthetic scale-test corpus (Faker identities only). Usage: gen.py OUTDIR SCALE (1 = about 900 MB)"""
import sys, os, io, random, mailbox, json, zipfile, time
sys.path.insert(0, "/opt/scrubber/tests")
from email import policy
from fixtures.synth import Synth
from fixtures.synth_docs import SlackWorkspace, docx_bytes, pdf_bytes
from fixtures import synth_images as si

out, scale = sys.argv[1], float(sys.argv[2])
s = Synth(4242)
people = [s.person() for _ in range(3000)]
rng = random.Random(1)
pick = lambda k=1: rng.sample(people, k)
t0 = time.time()

def mk(d):
    os.makedirs(os.path.join(out, d), exist_ok=True)
    return os.path.join(out, d)

# 1. Email: several mbox folders + loose .eml
email = mk("email")
pool = []
for i in range(3000):
    a, b = pick(2)
    pool.append(s.eml(a, b, html=rng.random() < 0.6, csv_attachment=rng.random() < 0.05).as_bytes(policy=policy.default))
for f in range(6):
    target = int(50e6 * scale)
    with open(os.path.join(email, f"folder{f}.mbox"), "wb") as fh:
        size = 0
        i = 0
        while size < target:
            data = pool[(i * 7 + f * 131) % len(pool)].replace(b"\nFrom ", b"\n>From ")
            fh.write(b"From MAILER-DAEMON Thu Jan  1 00:00:00 2022\n" + data + b"\n\n")
            size += len(data)
            i += 1
print("email", time.time() - t0, flush=True)

# 2. CSV/XLSX
crm = mk("crm")
for f in range(4):
    with open(os.path.join(crm, f"export{f}.csv"), "w", newline="") as fh:
        fh.write(",".join(s.HEADERS) + "\r\n")
        import csv as _csv
        w = _csv.writer(fh, lineterminator="\r\n")
        size = 0
        while size < 50e6 * scale:
            row = s.row(rng.choice(people))
            w.writerow(row)
            size += sum(len(x) for x in row) + 22
for f in range(int(20 * scale)):
    open(os.path.join(crm, f"sheet{f}.xlsx"), "wb").write(s.xlsx_bytes(pick(200)))
print("crm", time.time() - t0, flush=True)

# 3. Notes (plain text)
notes = mk("notes")
for f in range(int(200 * scale)):
    open(os.path.join(notes, f"note{f}.txt"), "w").write("\n".join(s.note(p) for p in pick(60)))
print("notes", time.time() - t0, flush=True)

# 4. Documents
docs = mk("documents")
for f in range(int(100 * scale)):
    open(os.path.join(docs, f"letter{f}.docx"), "wb").write(docx_bytes(s, pick(3), cases=[s.note(p) for p in pick(8)]))
    open(os.path.join(docs, f"summary{f}.pdf"), "wb").write(pdf_bytes(s, pick(3), cases=sum((s.note(p).splitlines() for p in pick(20)), [])))
print("documents", time.time() - t0, flush=True)

# 5. Images, scans, DICOM
imgs = mk("images")
for f in range(int(60 * scale)):
    p = rng.choice(people)
    open(os.path.join(imgs, f"badge{f}.jpg"), "wb").write(si.jpeg_with_gps(si.id_badge(p)))
    open(os.path.join(imgs, f"note{f}.png"), "wb").write(si.save(si.text_image(si.note_lines(s, p)), "PNG"))
for f in range(int(20 * scale)):
    pages = [si.text_image(si.note_lines(s, p) * 4) for p in pick(3)]
    open(os.path.join(imgs, f"scan{f}.pdf"), "wb").write(si.scanned_pdf(pages))
    a, b = pick(2)
    open(os.path.join(imgs, f"study{f}.dcm"), "wb").write(si.dicom_bytes(s, a, b))
print("images", time.time() - t0, flush=True)

# 6. Slack
slack = mk("slack")
data, _ = SlackWorkspace(s, pick(12)).zip_bytes(days=int(150 * scale))
open(os.path.join(slack, "workspace-export.zip"), "wb").write(data)
print("slack", time.time() - t0, flush=True)
