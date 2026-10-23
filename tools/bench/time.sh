#!/bin/sh
# Time the scrubber on each folder of a corpus, from a fresh state each time.
#   tools/bench/time.sh /path/to/corpus /path/to/results
# The corpus folder has one subfolder per category (as made by gen.py).
set -e
IN=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd)
for dir in "$IN"/*/; do
  cat=$(basename "$dir"); R="$OUT/$cat"; rm -rf "$R"; mkdir -p "$R/output" "$R/work" "$R/state"
  start=$(date +%s)
  docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
    -v "$dir:/input:ro" -v "$R/output:/output" -v "$R/work:/work" -v "$R/state:/state" \
    datafrenzy/local-scrubber:0.1.0 run > "$R/log.txt" 2>&1
  end=$(date +%s)
  echo "$cat seconds=$((end - start)) input_kb=$(du -sk "$dir" | cut -f1) $(grep Finished "$R/log.txt")"
done
