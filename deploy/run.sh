#!/bin/bash
# Боевой цикл на VPS (/opt/avito-bridge/run.sh, зовётся avito-bridge.timer ~каждые 3.5ч):
# фид кондиционеров + фид венков → nginx staticfiles (атомарно, через .tmp).
set -e
cd /opt/avito-bridge
mkdir -p state
exec 9>state/profile-publish.lock
if ! flock -n 9; then
  echo "another Avito Bridge publication is already running; skipped" >&2
  exit 75
fi
. .venv/bin/activate
TMP=""
trap 'if [ -n "$TMP" ]; then rm -f -- "$TMP"; fi' EXIT
PYTHONPATH=src python -m avito_bridge
TMP="$(mktemp --tmpdir=/opt/oasis/staticfiles .avito-feed.xml.XXXXXXXX.tmp)"
install -m 0644 -- feed_out/feed.xml "$TMP"
mv -f -- "$TMP" /opt/oasis/staticfiles/avito-feed.xml
TMP=""
echo "published $(date -Is)"

# Венки (отдельный профиль/аккаунт): ошибка сборки НЕ валит кондиционерный цикл —
# основной фид к этому моменту уже опубликован, венки просто останутся прошлой версии.
if PYTHONPATH=src python -m avito_bridge --config profiles/wreaths.yaml; then
  TMP="$(mktemp --tmpdir=/opt/oasis/staticfiles .avito-feed-wreaths.xml.XXXXXXXX.tmp)"
  install -m 0644 -- feed_out/wreaths.xml "$TMP"
  mv -f -- "$TMP" /opt/oasis/staticfiles/avito-feed-wreaths.xml
  TMP=""
  echo "published wreaths $(date -Is)"
else
  echo "WARN: сборка фида венков не удалась — пропущена, кондиционеры опубликованы" >&2
fi
