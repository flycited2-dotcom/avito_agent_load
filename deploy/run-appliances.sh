#!/bin/bash
# Быстрый путь после готовой карточки МБТ/КБТ. Не пересобирает кондиционеры:
# берёт их последний валидный feed_out/feed.xml и атомарно обновляет общий фид.
set -e
cd /opt/avito-bridge
mkdir -p state
exec 9>state/profile-publish.lock
if ! flock -n 9; then
  echo "another Avito Bridge publication is already running; skipped" >&2
  exit 75
fi
. .venv/bin/activate
if ! PYTHONPATH=src python -m avito_bridge --config profiles/appliances-btopt.yaml; then
  echo "appliance feed not ready; existing public feed preserved" >&2
  exit 0
fi
test -s feed_out/feed.xml
TMP="$(mktemp --tmpdir=/opt/oasis/staticfiles .avito-feed.xml.XXXXXXXX.tmp)"
trap 'rm -f -- "$TMP"' EXIT
PYTHONPATH=src python -m avito_bridge.feed.merge \
  --input feed_out/feed.xml \
  --input feed_out/appliances-btopt.xml \
  --output "$TMP"
mv -f -- "$TMP" /opt/oasis/staticfiles/avito-feed.xml
trap - EXIT
echo "published appliances $(date -Is)"
