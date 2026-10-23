# Build the Windows app on a Windows PC (the GitHub workflow does the same steps).
# Needs: Python 3.11, Tesseract (choco install tesseract), Inno Setup (choco install innosetup).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")
python -m pip install -r requirements.txt -r packaging\windows\requirements-windows.txt
python -m pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl
New-Item -ItemType Directory -Force dist | Out-Null; git archive --format=zip -o dist/source.zip HEAD
pyinstaller packaging\windows\DataFrenzyScrubber.spec --noconfirm --distpath dist --workpath build
Copy-Item "C:\Program Files\Tesseract-OCR" "dist\DataFrenzyScrubber\_internal\tesseract" -Recurse -Force
& "dist\DataFrenzyScrubber\DataFrenzyScrubber.exe" --self-test --self-test-out selftest.json | Out-Null
Get-Content selftest.json
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 packaging\windows\installer.iss
Write-Host "Installer: packaging\windows\Output\"
