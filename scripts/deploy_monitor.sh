#!/bin/bash
set -euo pipefail
cd /opt/avito-bridge
stage=/opt/avito-bridge/.deploy/monitor-20260920
backup="$(mktemp -d /opt/avito-bridge/state/monitor-deploy-20260920-XXXXXX)"
echo "backup=$backup"
systemctl stop avito-manual-stop-sync.timer
# Wait for any already-running reader to complete before replacing its modules.
while systemctl is-active --quiet avito-manual-stop-sync.service; do sleep 2; done
exec 9>state/profile-publish.lock
flock -w 180 9
cp -a src/avito_bridge/avito/client.py src/avito_bridge/avito/manual_stop.py src/avito_bridge/itp_stock_gate.py "$backup/"
cp -a state/avito-status-main.json state/manual-stop-main.json /opt/oasis/staticfiles/avito-feed.xml "$backup/"
cp -a /etc/systemd/system/avito-manual-stop-sync.service "$backup/"
install -m 0644 "$stage/client.py" src/avito_bridge/avito/client.py
install -m 0644 "$stage/manual_stop.py" src/avito_bridge/avito/manual_stop.py
install -m 0644 "$stage/itp_stock_gate.py" src/avito_bridge/itp_stock_gate.py
install -m 0644 "$stage/monitor.py" src/avito_bridge/monitor.py
install -m 0644 "$stage/monitor-main.yaml" config/monitor-main.yaml
install -m 0644 "$stage/avito-manual-stop-sync.service" /etc/systemd/system/avito-manual-stop-sync.service
install -m 0644 "$stage/avito-monitor@.service" "$stage/avito-monitor-poll.timer" "$stage/avito-monitor-daily.timer" /etc/systemd/system/
.venv/bin/python -m py_compile src/avito_bridge/avito/client.py src/avito_bridge/avito/manual_stop.py src/avito_bridge/itp_stock_gate.py src/avito_bridge/monitor.py
systemctl daemon-reload
systemctl start avito-manual-stop-sync.timer
echo 'installed; monitoring timers not enabled yet'
