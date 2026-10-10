#!/bin/bash
# Refresh JAC stock, then atomically make the same validated snapshot visible
# to the SplitHub bot and Avito Bridge.  A failed scrape leaves the previous
# snapshot intact, so Bridge's freshness check still blocks unsafe publishing.
set -euo pipefail

SCRAPER_ROOT=/opt/ostatki_mdv_b2b
SOURCE="$SCRAPER_ROOT/data/jac_stock_latest.json"
TARGET_DIR=/opt/splithub_api_telegram/data
TARGET="$TARGET_DIR/jac_stock_latest.json"

cd "$SCRAPER_ROOT"
PYTHONUTF8=1 NO_PROXY='*' .venv/bin/python -m jac_scraper scrape
test -s "$SOURCE"

mkdir -p "$TARGET_DIR"
temporary="$(mktemp --tmpdir="$TARGET_DIR" .jac_stock_latest.json.XXXXXXXX.tmp)"
trap 'rm -f -- "$temporary"' EXIT
install -m 0640 -- "$SOURCE" "$temporary"
mv -f -- "$temporary" "$TARGET"
trap - EXIT
