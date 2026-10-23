# Packet for a privacy / HIPAA expert

Give this to a HIPAA privacy advisor before the first sale. Buyers may also ask for a formal **Expert Determination** (45 CFR 164.514(b)(1)) on top of Safe Harbor. This packet is what that expert would start from.

## What to send

1. `output/reports/deidentification-report.pdf` from a pilot run (or the synthetic demo run). It covers the method, the 18-identifier checklist with counts, verification results, review decisions, configuration hash and software versions.
2. `output/manifest.csv`: what the export contains.
3. `docs/TECHNICAL.md`: how each identifier and file format is handled, and the known limitations.
4. Test evidence:
   - `scripts/test.sh` output (211 tests on synthetic data)
   - the recall table in `docs/TECHNICAL.md`
   - the speed results in `docs/PERFORMANCE.md`
   - `face-evaluation.csv`, once it has been run on a synthetic face set (see `docs/TECHNICAL.md`)
5. `README.md`: what the seller is told to do.
6. The pilot feedback form (`docs/PILOT.md`) with the seller's measured misses.

## Questions for the expert

1. **Consistent pseudonyms.** The same person gets the same token (`PERSON_0412`) across all files, so conversations stay coherent. The token number is assigned in order of first appearance. A keyed HMAC of the name is used only as a lookup key in a table that stays on the seller's machine and is never disclosed. Do these tokens meet 45 CFR 164.514(c)? That section allows a re-identification code if it is (1) not derived from information about the individual and (2) neither used nor disclosed for other purposes, with the re-identification mechanism also kept undisclosed. Or should tokens be re-keyed per buyer, or per export?
2. **Dates.**
   - Dates become the year.
   - Slack message timestamps move to January 1 of their year. Each still gets a unique value inside that day, so threads keep working.
   - Message *order* within a file is kept.

   Does order within a year, or the number of messages per year, reveal anything Safe Harbor cares about?
3. **Ages over 89 and birth years.** Birth dates that imply age 90+ become "90+" (in DICOM, the birth date is removed and the age is `090Y`). Other years of birth stay as the year. Is that consistent with the expert's reading?
4. **ZIP codes.** Three-digit ZIPs, with the 17 HHS-listed restricted areas set to 000, use the HHS 2000-Census list. Should the list be refreshed with 2020 Census data?
5. **Large public organizations.** About 40 large payers, vendors and agencies (UnitedHealthcare, Microsoft, Medicare …) are kept by default. Employers of individuals are otherwise removed. Is the default list acceptable?
6. **Context risk ("no actual knowledge").** The tool flags unique roles, rare conditions together with places, small towns, distinctive events and relationship references for human review. Are there categories it should add for the seller's specialty?
7. **Residual error.** Automated detection is imperfect. Recall on synthetic text is 98–100% by type (held-out names: 98%), and real-data recall comes from the pilot. What error rate, sampling plan and review process would the expert accept, and does the buyer's intended use push this toward Expert Determination?
8. **Images.**
   - Faces are pixelated, and on-image text is OCR'd and blacked out.
   - Text the OCR can't read, low-confidence text and handwriting-like text go to human review.
   - 3-D head scans (CT/MR volumes) are not defaced and are flagged in the documentation.

   Is a policy of excluding head volumes needed?
9. **Free text that isn't an identifier but is rare.** Examples: a unique tattoo, a distinctive job. These are covered only partly, by the context-risk lists. Is a seller attestation enough, or should a sampling review be mandatory?

## Threat model in one paragraph

The scrubber runs offline on the seller's computer. The originals, the HMAC key and the token table never leave that machine. The buyer receives only the scrubbed files, the manifest and the report. The remaining risks are:
- identifiers the detectors miss
- quasi-identifiers in free text (handled by context-risk review and the seller's attestation)
- linkage across exports if the same key were reused for several buyers (see question 1)
- unreadable embedded content (scanned text the OCR can't read; vector-drawn text in PDFs)
