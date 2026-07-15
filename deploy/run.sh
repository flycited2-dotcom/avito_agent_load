#!/bin/bash
# Боевой цикл на VPS (/opt/avito-bridge/run.sh, зовётся avito-bridge.timer ~каждые 3.5ч):
# фид кондиционеров + фид венков → nginx staticfiles (атомарно, через .tmp).
set -e
cd /opt/avito-bridge
. .venv/bin/activate
PYTHONPATH=src python -m avito_bridge
TMP=/opt/oasis/staticfiles/.avito-feed.xml.tmp
cp feed_out/feed.xml "$TMP"
mv -f "$TMP" /opt/oasis/staticfiles/avito-feed.xml
echo "published $(date -Is)"

# Венки (профиль №2, аккаунт 402 499 218): ошибка сборки НЕ валит кондиционерный цикл —
# основной фид к этому моменту уже опубликован, венки просто останутся прошлой версии.
if PYTHONPATH=src python -m avito_bridge --config profiles/wreaths.yaml; then
  TMPW=/opt/oasis/staticfiles/.avito-feed-wreaths.xml.tmp
  cp feed_out/wreaths.xml "$TMPW"
  mv -f "$TMPW" /opt/oasis/staticfiles/avito-feed-wreaths.xml
  echo "published wreaths $(date -Is)"
else
  echo "WARN: сборка фида венков не удалась — пропущена, кондиционеры опубликованы" >&2
fi
