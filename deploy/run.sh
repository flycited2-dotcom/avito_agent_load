#!/bin/bash
# Боевой цикл на VPS (/opt/avito-bridge/run.sh, зовётся avito-bridge.timer ~каждые 3.5ч):
# фид кондиционеров + готовые карточки МБТ/КБТ → общий XML основного аккаунта,
# а венки остаются отдельным аккаунтом.
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
# If the main-account API credentials are configured, capture a manual removal
# immediately before rebuilding. A failed status read aborts publication and
# preserves the previous public XML instead of risking a republish.
MAIN_AVITO_CLIENT_ID="${AVITO_CLIENT_ID:-${AVITO_MAIN_CLIENT_ID:-}}"
MAIN_AVITO_CLIENT_SECRET="${AVITO_CLIENT_SECRET:-${AVITO_MAIN_CLIENT_SECRET:-}}"
if [ -n "$MAIN_AVITO_CLIENT_ID" ] && [ -n "$MAIN_AVITO_CLIENT_SECRET" ]; then
  PYTHONPATH=src python -m avito_bridge.avito.manual_stop \
    --feed /opt/oasis/staticfiles/avito-feed.xml \
    --stop state/manual-stop-main.json \
    --observations state/avito-status-main.json
fi
PYTHONPATH=src python -m avito_bridge
TMP="$(mktemp --tmpdir=/opt/oasis/staticfiles .avito-feed.xml.XXXXXXXX.tmp)"
if PYTHONPATH=src python -m avito_bridge --config profiles/appliances-btopt.yaml; then
  PYTHONPATH=src python -m avito_bridge.feed.merge \
    --input feed_out/feed.xml \
    --input feed_out/appliances-btopt.xml \
    --output "$TMP"
  PYTHONPATH=src python -m avito_bridge.feed.suppress \
    --input "$TMP" \
    --output "$TMP" \
    --stop state/manual-stop-main.json
  echo "merged appliances $(date -Is)"
else
  # После первой публикации составного фида нельзя заменять его
  # conditioners-only: Avito воспримет исчезнувшие товары как снятые.
  # При любой ошибке appliance-сборки оставляем публичный XML целиком прежним.
  rm -f -- "$TMP"
  TMP=""
  echo "WARN: appliance feed not ready; existing public feed preserved" >&2
fi
if [ -n "$TMP" ]; then
  mv -f -- "$TMP" /opt/oasis/staticfiles/avito-feed.xml
  TMP=""
  echo "published $(date -Is)"
fi

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
