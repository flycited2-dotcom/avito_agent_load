"""Apply the owner's explicit 2026-09-20 exclusion of exactly three items."""
import fcntl
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from avito_bridge.avito.manual_stop import _feed_items, _read_json, _write_json_atomic, suppress_feed

root = Path('/opt/avito-bridge')
feed = Path('/opt/oasis/staticfiles/avito-feed.xml')
stop = root / 'state/manual-stop-main.json'
targets = {
    'adab767002158a46ab14850f': 'Микроволновая печь LG MS2042DB',
    '52ba88b7bcd858029a6ad0d6': 'Микроволновая печь LG MS2044V',
    'b1d97fbe1559653c9768b2e0': 'Микроволновая печь GE MS120W',
}
with (root / 'state/profile-publish.lock').open('a') as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    current = _feed_items(feed)
    for aid, title in targets.items():
        if aid in current and current[aid] != title:
            raise ValueError('Product title mismatch; refusing changes')
    now = datetime.now(timezone.utc).isoformat()
    backup = root / 'state/microwaves-owner-stop-20260920'
    backup.mkdir(exist_ok=True)
    if not (backup / 'feed-before.xml').exists():
        shutil.copy2(feed, backup / 'feed-before.xml')
        shutil.copy2(stop, backup / 'stop-before.json')
    data = _read_json(stop, {'version': 1, 'entries': {}})
    for aid, title in targets.items():
        data['entries'].setdefault(aid, {'ad_id': aid, 'title': title, 'created_at': now,
                                       'reason': 'owner_confirmed_out_of_stock_20260920'})
    data['updated_at'] = now
    _write_json_atomic(stop, data)
    removed, remaining = suppress_feed(feed, feed, stop)
    assert not (set(_feed_items(feed)) & set(targets))
    print(json.dumps({'removed': removed, 'remaining': remaining, 'backup': str(backup)}))
