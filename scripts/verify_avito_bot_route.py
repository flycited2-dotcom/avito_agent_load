"""Verify the configured bot and deliver one idempotent owner-only route test."""
import fcntl
import json
import time
from pathlib import Path

import httpx
import yaml
from avito_bridge.monitor import Monitor, telegram_settings, error_label


def main():
    cfg = yaml.safe_load(Path('config/monitor-main.yaml').read_text())
    token, owner, base = telegram_settings(cfg)
    with httpx.Client(timeout=15) as http:
        for attempt in range(3):
            try:
                response = http.get(f'{base}/bot{token}/getMe')
                response.raise_for_status()
                me = response.json()
                response = http.post(f'{base}/bot{token}/getChat', json={'chat_id': owner})
                response.raise_for_status()
                chat = response.json()
                break
            except (httpx.ConnectTimeout, httpx.ConnectError):
                if attempt == 2:
                    raise
                time.sleep(2)
        if not me.get('ok') or me['result']['id'] != cfg['telegram_bot_id']:
            raise ValueError('Unexpected bot identity')
        if not chat.get('ok') or chat['result']['type'] != 'private':
            raise ValueError('Private owner chat not available in the new bot')
    print(json.dumps({'bot_id': me['result']['id'], 'username': me['result']['username'], 'chat_type': 'private'}), flush=True)
    with Path('state/monitor-main.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        monitor = Monitor(cfg, None, notify=True)
        # A partially delivered notification must start over in the NEW bot.
        if monitor.state.get('delivery_bot_id') != cfg['telegram_bot_id']:
            for event in monitor.state['outbox'].values():
                event.pop('sent_parts', None)
            monitor.state['delivery_bot_id'] = cfg['telegram_bot_id']
        monitor.event('route-confirmed:' + str(cfg['telegram_bot_id']), 1,
            'Уведомления Авито перенастроены на этого бота (ID 8650114784). '
            'Новые оповещения и ежедневный отчёт в 09:00 МСК будут приходить сюда. '
            'Отправка уведомлений Авито через прежнего бота отключена. Это контрольное сообщение.')
        monitor.flush()
        print(json.dumps({'pending_notifications': len(monitor.state['outbox']), 'last_delivery': monitor.state.get('last_delivery')}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('route_verification_failed: ' + error_label(exc), flush=True)
        raise SystemExit(1)
