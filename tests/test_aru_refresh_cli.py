"""ARU refresh compiles by default; installation needs the owner's explicit flag."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def fixture(tmp_path, monkeypatch, flags=()):
    script = Path(__file__).resolve().parents[1] / 'scripts/refresh_aru_account.py'
    spec = importlib.util.spec_from_file_location('aru_refresh_under_test', script)
    refresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(refresh)
    bridge, factory = tmp_path / 'bridge', tmp_path / 'factory'
    monkeypatch.setattr(refresh, 'BRIDGE', bridge)
    monkeypatch.setattr(refresh, 'CF', factory)
    pages = tmp_path / 'pages'
    pages.mkdir()
    category = '/elektroinstrument/'
    (pages / '001.json').write_text(json.dumps({
        'category_id': category, 'page': 1, 'pages': 1, 'authenticated': True,
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'url': 'https://aru.ooo' + category, 'title': 'Электроинструмент',
        'items': [{'id': '42', 'name': 'Дрель TOOL D1', 'article': 'D1',
                   'url': 'https://aru.ooo/elektroinstrument/drill/',
                   'stock_text': 'В наличии', 'price_text': '100.01 ₽'}]
    }), encoding='utf-8')
    categories = tmp_path / 'categories.json'
    categories.write_text(json.dumps([{'id': category, 'name': 'Электроинструмент',
                                      'url': 'https://aru.ooo' + category}]), encoding='utf-8')
    output = tmp_path / 'candidate'
    monkeypatch.setattr(refresh.sys, 'argv', [str(script), '--pages-dir', str(pages),
                        '--output-dir', str(output), '--category-crawl',
                        '--categories', str(categories), *flags])
    live = [factory / 'state/prices/aru-catalog.json', bridge / 'runtime/aru/catalog.json']
    for path in live:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('previous-valid-snapshot', encoding='utf-8')
    return refresh, output, live, pages


@pytest.mark.parametrize('flags', [(), ('--compile-only',)])
def test_compile_modes_never_run_ssh_or_replace_live_snapshots(tmp_path, monkeypatch, capsys, flags):
    refresh, output, live, _ = fixture(tmp_path, monkeypatch, flags)
    def forbidden(*args, **kwargs):
        raise AssertionError('Compilation must not execute SSH or any external command')
    monkeypatch.setattr(refresh.subprocess, 'run', forbidden)
    refresh.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'compiled'
    candidate = json.loads((output / 'catalog.json').read_text(encoding='utf-8'))
    assert candidate['items'][0]['content_price_rub'] == 111
    assert all(path.read_text(encoding='utf-8') == 'previous-valid-snapshot' for path in live)
    assert not (output / 'refresh-result.json').exists()


def test_explicit_install_confirmed_is_only_mode_reaching_installer(tmp_path, monkeypatch, capsys):
    refresh, output, live, _ = fixture(tmp_path, monkeypatch, ('--install-confirmed',))
    calls = []
    def fake_run(arguments, **kwargs):
        # Only a fake SSH transport: never accesses a server or real filesystem.
        assert arguments[:2] == ['ssh', 'sprintbox-itp']
        calls.append((arguments, kwargs))
        return SimpleNamespace(stdout=b'{"status":"installed"}')
    monkeypatch.setattr(refresh.subprocess, 'run', fake_run)
    refresh.main()
    assert len(calls) == 3
    assert 'install_aru_snapshot.py' in calls[-1][0][-1]
    assert json.loads(capsys.readouterr().out)['status'] == 'installed'
    assert all(json.loads(path.read_text(encoding='utf-8'))['items'][0]['id'] == '42' for path in live)
    assert json.loads((output / 'refresh-result.json').read_text(encoding='utf-8'))['status'] == 'installed'


def test_compile_only_and_install_confirmed_are_mutually_exclusive(tmp_path, monkeypatch, capsys):
    refresh, output, live, _ = fixture(tmp_path, monkeypatch, ('--compile-only', '--install-confirmed'))
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid flags must not reach SSH')
    monkeypatch.setattr(refresh.subprocess, 'run', forbidden)
    with pytest.raises(SystemExit) as error:
        refresh.main()
    assert error.value.code == 2
    assert 'not allowed with argument' in capsys.readouterr().err
    assert not (output / 'catalog.json').exists()
    assert all(path.read_text(encoding='utf-8') == 'previous-valid-snapshot' for path in live)


def test_invalid_capture_cannot_install_even_with_confirmed_flag(tmp_path, monkeypatch):
    refresh, output, live, pages = fixture(tmp_path, monkeypatch, ('--install-confirmed',))
    captured = json.loads((pages / '001.json').read_text(encoding='utf-8'))
    captured['authenticated'] = False
    (pages / '001.json').write_text(json.dumps(captured), encoding='utf-8')
    def forbidden(*args, **kwargs):
        raise AssertionError('Failed validation must not reach SSH')
    monkeypatch.setattr(refresh.subprocess, 'run', forbidden)
    with pytest.raises(ValueError, match='unauthenticated'):
        refresh.main()
    assert not (output / 'catalog.json').exists()
    assert all(path.read_text(encoding='utf-8') == 'previous-valid-snapshot' for path in live)
