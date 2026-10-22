# Bundled reference data

All files here ship inside the image. Nothing is downloaded at runtime.

| File | Source | License |
|---|---|---|
| `zip3_restricted.csv` | HHS OCR, *Guidance Regarding Methods for De-identification of PHI* (2012), Safe Harbor section on ZIP codes: the 17 three-digit ZIP areas with 20,000 or fewer people (2000 Census). These are replaced with `000`. | Public domain (US Gov) |
| `census_first_names.txt` | US Census Bureau, 1990 Census frequently occurring first names (`dist.female.first` + `dist.male.first`), names only. | Public domain (US Gov) |
| `census_surnames.txt` | US Census Bureau, 2010 Census surnames (`Names_2010Census.csv`), names that occur 300+ times. | Public domain (US Gov) |

To update the ZIP list (e.g. with 2020 Census data), replace `zip3_restricted.csv`, keeping the `zip3` column. The config hash in the audit report changes when this data changes.
| `models/face_detection_yunet_2023mar.onnx` | OpenCV Zoo YuNet face detector (github.com/opencv/opencv_zoo, `models/face_detection_yunet`), sha256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`. | MIT |
