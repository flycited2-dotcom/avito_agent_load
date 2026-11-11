import json
from datetime import timedelta

import httpx
import pytest

from avito_bridge.monitor import Monitor, error_label, metric_map, spending_total, telegram_settings


def monitor(tmp_path):
    feed = tmp_path / 'feed.xml'
    feed.write_text('<Ads><Ad><Id>a</Id><Title>Product</Title></Ad></Ads>', encoding='utf-8')
    observations = tmp_path / 'observations.json'
    observations.write_text(json.dumps({'observations': {'a': {'avito_id': 1, 'status': 'active'}}}))
    return Monitor({'account_id': 42, 'state_dir': str(tmp_path), 'feed': str(feed),
                    'observations': str(observations), 'stop': str(tmp_path / 'stop.json'),
                    'low_balance_rub': 500, 'daily_spending_alert_rub': 1000}, None)


def test_missing_metrics_are_not_zero_and_spendings_are_rubles():
    assert metric_map({'metrics': [{'slug': 'views', 'value': 5}]}) == {'views': 5}
    assert spending_total({'result': {'groupings': [{'spendings': [
        {'slug': 'presence', 'value': 123.45}, {'slug': 'promotion', 'value': 2}]}]}}) == 125.45


def test_outbox_persisted_deduplicated_and_disabled_delivery(tmp_path):
    m = monitor(tmp_path)
    m.event('one', 1, 'hello')
    m.event('one', 1, 'hello')
    m.flush()
    assert len(json.loads(m.path.read_text())['outbox']) == 1
    restored = monitor(tmp_path)
    restored.event('one', 1, 'hello')
    assert len(restored.state['outbox']) == 1


def test_chats_baseline_no_old_alerts_new_incoming_once(tmp_path):
    m = monitor(tmp_path)
    rows = [{'id': 'c1', 'last_message': {'id': 'old', 'direction': 'in'},
             'context': {'value': {'title': 'Product'}}}]
    m.get = lambda *a, **k: {'chats': rows}
    m.chats()
    assert len(m.state['outbox']) == 1  # baseline only
    m.chats()
    assert len(m.state['outbox']) == 1
    rows[0]['last_message']['id'] = 'new'
    m.chats()
    assert len(m.state['outbox']) == 2
    rows[0]['last_message'] = {'id': 'out', 'direction': 'out'}
    m.chats()
    assert len(m.state['outbox']) == 2


def test_calls_seed_then_zero_talk_alert_without_customer_phone(tmp_path):
    m = monitor(tmp_path)
    rows = [{'callId': 1, 'callTime': m.now.isoformat(), 'talkDuration': 0, 'buyerPhone': 'SECRET'}]
    m.get = lambda *a, **k: {'calls': rows}
    m.calls()
    assert not m.state['outbox']
    rows.append({**rows[0], 'callId': 2})
    m.calls()
    assert len(m.state['outbox']) == 1
    assert 'SECRET' not in json.dumps(m.state)
    m.calls()
    assert len(m.state['outbox']) == 1


def test_daily_missing_data_no_false_zeros_and_idempotent(tmp_path):
    m = monitor(tmp_path)
    m.get = lambda *a, **k: {'result': {'dataTotalCount': 1, 'groupings': [
        {'id': 1, 'metrics': [{'slug': 'views', 'value': 40}, {'slug': 'contacts', 'value': 4}]}]}}
    m.daily()
    text = list(m.state['outbox'].values())[0]['text']
    assert 'Расходы: нет полных данных' in text
    assert 'сначала подтвердить остаток и маржу' in text
    m.get = lambda *a, **k: pytest.fail('Should use durable daily report')
    m.daily()
    assert len(m.state['outbox']) == 1


def test_conditions_send_once_and_allow_later_regression(tmp_path):
    m = monitor(tmp_path)
    m.condition('balance', True, 'bad')
    m.condition('balance', True, 'bad')
    assert len(m.state['outbox']) == 1
    m.condition('balance', False, '')
    assert len(m.state['outbox']) == 2
    m.now += timedelta(minutes=10)
    m.condition('balance', True, 'bad')
    assert len(m.state['outbox']) == 3


def test_stale_autoload_alert_is_not_repeated_and_recovers(tmp_path):
    m = monitor(tmp_path)
    upload = {'upload_id': 42, 'status': 'success_warning',
              'started_at': (m.now - timedelta(days=2)).isoformat()}
    m.state['cache']['autoload'] = {'upload_id': 42}
    m.get = lambda *a, **k: upload
    m.autoload()
    m.autoload()
    assert len(m.state['outbox']) == 1
    assert 'более 2 часов' in next(iter(m.state['outbox'].values()))['text']
    upload['started_at'] = m.now.isoformat()
    m.autoload()
    assert len(m.state['outbox']) == 2


def test_exception_url_never_leaks_token():
    response = httpx.Response(403, request=httpx.Request('POST', 'https://example.org/botSECRET/sendMessage'))
    with pytest.raises(httpx.HTTPStatusError) as caught:
        response.raise_for_status()
    assert error_label(caught.value) == 'HTTP 403'


def test_bot_route_uses_selected_token_but_existing_private_owner(tmp_path):
    bot_env = tmp_path / 'bot.env'
    owner_env = tmp_path / 'owner.env'
    bot_env.write_text('B2B_TOKEN=8650114784:test-only\n', encoding='utf-8')
    owner_env.write_text('TELEGRAM_OWNER_CHAT_ID=123\n', encoding='utf-8')
    cfg = {'telegram_env': str(bot_env), 'telegram_owner_env': str(owner_env),
           'telegram_token_key': 'B2B_TOKEN', 'telegram_bot_id': 8650114784}
    assert telegram_settings(cfg) == ('8650114784:test-only', '123', 'https://api.telegram.org')
    cfg['telegram_bot_id'] = 8673052162
    with pytest.raises(ValueError, match='mismatch'):
        telegram_settings(cfg)
