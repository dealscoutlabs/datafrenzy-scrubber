#!/bin/sh
# Build the Mac app and its .dmg.   packaging/macos/build.sh [python]
# Needs: Python 3.11 with requirements + pywebview + pyinstaller, Homebrew Tesseract.
set -e
cd "$(dirname "$0")/../.."
PY=${1:-python3}
APP="dist/Data Frenzy Scrubber.app"
mkdir -p dist && git archive --format=zip -o dist/source.zip HEAD   # AGPL: source ships with the app
"$PY" -m PyInstaller packaging/macos/DataFrenzyScrubber.spec --noconfirm --distpath dist --workpath build
python3 packaging/macos/bundle_tesseract.py "$APP/Contents/Frameworks/tesseract" "$APP/Contents/Resources/tessdata"
# Sign everything inside out. MAC_SIGN_IDENTITY = "Developer ID Application: ..." signs for real
# (hardened runtime, timestamp); without it the app gets an ad-hoc signature for local use.
sh packaging/macos/sign.sh "$APP" "${MAC_SIGN_IDENTITY:--}"
# A Mac without Homebrew: only system folders on PATH, so the bundled OCR engine must be used
env PATH=/usr/bin:/bin:/usr/sbin:/sbin DYLD_FALLBACK_LIBRARY_PATH=/usr/lib "$APP/Contents/MacOS/DataFrenzyScrubber" --self-test --self-test-out selftest-mac.json >/dev/null 2>&1 || {
  cat selftest-mac.json 2>/dev/null; echo "self-test failed"; exit 1; }
cat selftest-mac.json
ARCH=$(uname -m)
rm -f "dist/DataFrenzyScrubber-mac-$ARCH.dmg"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist/DataFrenzyScrubber-mac-$ARCH.dmg"
hdiutil create -volname "Data Frenzy Scrubber" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
if [ -n "$MAC_SIGN_IDENTITY" ]; then
  codesign --force --timestamp -s "$MAC_SIGN_IDENTITY" "$DMG"
  if [ -n "$APPLE_ID" ] && [ -n "$APPLE_APP_PASSWORD" ] && [ -n "$APPLE_TEAM_ID" ]; then
    # Apple checks the app; on success the approval ("ticket") is attached to the .dmg, so the
    # Mac opens it without the "can't be opened" warning, even offline.
    xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --password "$APPLE_APP_PASSWORD" \
      --team-id "$APPLE_TEAM_ID" --wait --timeout 60m --output-format json > notarize.json || true
    cat notarize.json
    if ! grep -q '"status" *: *"Accepted"' notarize.json; then
      id=$(sed -n 's/.*"id" *: *"\([^"]*\)".*/\1/p' notarize.json | head -1)
      [ -n "$id" ] && xcrun notarytool log "$id" --apple-id "$APPLE_ID" --password "$APPLE_APP_PASSWORD" \
        --team-id "$APPLE_TEAM_ID"
      echo "notarization failed"; exit 1
    fi
    xcrun stapler staple "$DMG"
    spctl -a -t open --context context:primary-signature -v "$DMG"
  fi
fi
echo "dmg: $DMG"
