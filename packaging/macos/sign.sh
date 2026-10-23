#!/bin/sh
# Sign every binary inside the app, inside out, then the app itself.
#   packaging/macos/sign.sh "dist/Data Frenzy Scrubber.app" [identity]
# identity "-" (the default) is an ad-hoc signature; with a "Developer ID Application: ..." identity
# the hardened runtime and a secure timestamp are added, as notarization requires.
set -e
APP=$1
ID=${2:--}
ENT="$(dirname "$0")/entitlements.plist"
if [ "$ID" = "-" ]; then OPTS=""; else OPTS="--options runtime --timestamp"; fi
# libraries and executables first (deepest paths first), the app bundle last
find "$APP/Contents" -type f | awk '{ print length($0) "\t" $0 }' | sort -rn | cut -f2- | while IFS= read -r f; do
  if file -b "$f" | grep -q "Mach-O"; then
    codesign --force $OPTS --entitlements "$ENT" -s "$ID" "$f" >/dev/null 2>&1 || {
      echo "could not sign $f"; codesign --force $OPTS --entitlements "$ENT" -s "$ID" "$f"; exit 1; }
  fi
done
codesign --force $OPTS --entitlements "$ENT" -s "$ID" "$APP"
codesign --verify --deep --strict "$APP"
echo "signed: $APP ($ID)"
