"""Keep the five owner-confirmed archived goods out of subsequent XML uploads."""
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
    '080742c0311e393638c46cb4': 'DEM-BY2',
    '346cbaf50d64ae9496aca98e': 'GL 2162',
    '3d910bb809186b797317a9f7': 'CT-1659',
    '790de6856d42450ac1651c7b': 'GL 2162',
    'fa40c2d4f02947f217dc2bcf': 'VT-8513',
}
with (root / 'state/profile-publish.lock').open('a') as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    current = _feed_items(feed)
    backup = root / 'state/archive-owner-stop-20260920'
    backup.mkdir(exist_ok=True)
    if not (backup / 'feed-before.xml').exists():
        shutil.copy2(feed, backup / 'feed-before.xml')
        shutil.copy2(stop, backup / 'stop-before.json')
    data = _read_json(stop, {'version': 1, 'entries': {}})
    now = datetime.now(timezone.utc).isoformat()
    for aid, model in targets.items():
        if aid in current and model not in current[aid]:
            raise ValueError('Model mismatch')
        if aid not in current and aid not in data['entries']:
            raise ValueError('Unknown target')
        data['entries'].setdefault(aid, {'ad_id': aid, 'title': current.get(aid, model),
            'created_at': now, 'reason': 'owner_confirmed_archive_hold_20260920'})
    data['updated_at'] = now
    _write_json_atomic(stop, data)
    removed, remaining = suppress_feed(feed, feed, stop)
    print(json.dumps({'removed': removed, 'remaining': remaining, 'total_stops': len(data['entries'])}))
