"""Server-side validated replacement of ARU data only, with backup and rollback."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.aru-refresh.tmp')
    temp.write_bytes(raw)
    os.chmod(temp, 0o644)
    os.replace(temp, path)


def main():
    candidate = Path(sys.argv[1]).resolve()
    raw = candidate.read_bytes()
    assert len(raw) <= 50 * 1024 * 1024
    data = json.loads(raw)
    assert data['source'] == 'aru' and data['schema_version'] == 1
    assert data['complete'] is True and data['authenticated'] is True
    if data.get('completeness_basis') == 'category_tree':
        from avito_bridge.ingest.aru_categories import validate_category_coverage
        validate_category_coverage(data)
    else:
        assert data['captured_pages'] == data['pages'] and 1 <= data['pages'] <= 1000
    assert data['price_basis'] == 'account_price' and 1 <= len(data['items']) <= 30000
    stamp = datetime.fromisoformat(data['generated_at'])
    assert stamp.tzinfo and -300 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 86400
    current = {}
    for row in data['items']:
        identity = str(row['id'])
        price = Decimal(str(row['price']))
        assert identity.isdigit() and identity not in current
        assert row['available'] is True and row['price_basis'] == 'account_price'
        assert '/pod-zakaz/' not in str(row.get('url', ''))
        assert price.is_finite() and price > 0
        current[identity] = price
    paths = [Path('/opt/content-factory/state/prices/aru-catalog.json'),
             Path('/opt/avito-bridge/runtime/aru/catalog.json')]
    previous_data = json.loads(paths[1].read_text()) if paths[1].exists() else {'items': []}
    previous = {str(r['id']): Decimal(str(r['price'])) for r in previous_data['items'] if r.get('price')}
    backup = candidate.parent / ('backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir()
    originals = {p: p.read_bytes() if p.exists() else None for p in paths}
    for index, path in enumerate(paths):
        if path.exists():
            shutil.copy2(path, backup / (str(index) + '-catalog.json'))
    try:
        for path in paths:
            atomic_write(path, raw)
        checks = [('/opt/content-factory',
                   'from content_factory.ingest.aru_account import load_account_items; '
                   f'assert len(load_account_items("state/prices")) == {len(current)}'),
                  ('/opt/avito-bridge',
                   'from avito_bridge.config import load_config; '
                   'from avito_bridge.ingest.aru_snapshot import fetch_aru_snapshot; '
                   f'assert len(fetch_aru_snapshot(load_config("profiles/aru.yaml"))) == {len(current)}')]
        for root, code in checks:
            subprocess.run([root + '/.venv/bin/python', '-c', code], cwd=root,
                           env={**os.environ, 'PYTHONPATH': 'src'}, check=True)
    except Exception:
        for path, old in originals.items():
            if old is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, old)
        raise
    report = {'status': 'updated', 'supplier_updated_at': data['generated_at'],
              'eligible_items': len(current), 'scanned': data['scanned_products'],
              'excluded': data['excluded_products'], 'added': len(current.keys() - previous.keys()),
              'removed': len(previous.keys() - current.keys()),
              'price_changed': sum(current[k] != previous[k] for k in current.keys() & previous.keys()),
              'backup': str(backup), 'published': 0}
    (candidate.parent / 'refresh-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
