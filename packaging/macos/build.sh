#!/bin/sh
# Build the Mac app and its .dmg.   packaging/macos/build.sh [python]
# Needs: Python 3.11 with requirements + pywebview + pyinstaller, Homebrew Tesseract.
set -e
cd "$(dirname "$0")/../.."
PY=${1:-python3}
APP="dist/Data Frenzy Scrubber.app"
mkdir -p dist && git archive --format=zip -o dist/source.zip HEAD   # AGPL: source ships with the app
"$PY" -m PyInstaller packaging/macos/DataFrenzyScrubber.spec --noconfirm --distpath dist --workpath build
python3 packaging/macos/bundle_tesseract.py "$APP/Contents/Frameworks/tesseract"
# Re-sign the whole app after adding files (ad-hoc until a Developer ID certificate exists;
# notarization goes here too).
codesign --force --deep -s - "$APP"
# A Mac without Homebrew: only system folders on PATH, so the bundled OCR engine must be used
env PATH=/usr/bin:/bin:/usr/sbin:/sbin "$APP/Contents/MacOS/DataFrenzyScrubber" --self-test --self-test-out selftest-mac.json >/dev/null 2>&1 || {
  cat selftest-mac.json 2>/dev/null; echo "self-test failed"; exit 1; }
cat selftest-mac.json
ARCH=$(uname -m)
rm -f "dist/DataFrenzyScrubber-mac-$ARCH.dmg"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Data Frenzy Scrubber" -srcfolder "$STAGE" -ov -format UDZO "dist/DataFrenzyScrubber-mac-$ARCH.dmg"
echo "dmg: dist/DataFrenzyScrubber-mac-$ARCH.dmg"
