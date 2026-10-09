"""Compile a complete browser capture and replace ARU prices locally and on VPS."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

BRIDGE = Path(__file__).resolve().parents[1]
CF = BRIDGE.parents[2] / 'content-factory'
sys.path.insert(0, str(CF / 'src'))
from content_factory.ingest.aru_account import build_account_snapshot
from content_factory.ingest.aru_site import save_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pages-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    pages = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(args.pages_dir.glob('*.json'))]
    data = build_account_snapshot(pages, reconcile_moved=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidate = args.output_dir / 'catalog.json'
    save_snapshot(candidate, data)
    stage = '/opt/avito-candidates/aru-refresh-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    subprocess.run(['ssh', 'sprintbox-itp', 'mkdir -p ' + stage], check=True)
    subprocess.run(['ssh', 'sprintbox-itp', 'cat > ' + stage + '/catalog.json'], input=candidate.read_bytes(), check=True)
    result = subprocess.run(['ssh', 'sprintbox-itp',
                             '/opt/avito-bridge/.venv/bin/python /opt/avito-bridge/scripts/install_aru_snapshot.py ' + stage + '/catalog.json'],
                            stdout=subprocess.PIPE, check=True)
    report = json.loads(result.stdout)
    save_snapshot(CF / 'state/prices/aru-catalog.json', data)
    save_snapshot(BRIDGE / 'runtime/aru/catalog.json', data)
    (args.output_dir / 'refresh-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
