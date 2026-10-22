#!/bin/sh
# Run the test suite in the network-less test container.
# (Same as `docker compose run --rm tests`, for machines without the compose plugin.)
set -e
cd "$(dirname "$0")/.."
docker build -q -f docker/scrubber.Dockerfile --target test -t datafrenzy/local-scrubber-tests:0.1.0 . >/dev/null
exec docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges datafrenzy/local-scrubber-tests:0.1.0 /opt/scrubber/tests "$@"
