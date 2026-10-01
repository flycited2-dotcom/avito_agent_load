"""Owner-only Avito monitoring. No listing, customer-message or paid API writes.

POST endpoints below retrieve reports; the only external mutation is sending
notifications to the configured owner via the existing stock-report Telegram bot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml
from decouple import Config, RepositoryEnv

from avito_bridge.avito.client import AvitoClient
from avito_bridge.avito.manual_stop import _feed_items, _read_json, _write_json_atomic
from avito_bridge.ready_price.server_run import autoload_health

MSK = timezone(timedelta(hours=3))


def signature(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def error_label(exc: Exception) -> str:
    # Never stringify exceptions containing request URLs (Telegram bot tokens).
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write_json_atomic(path, value)
    path.chmod(0o600)


def metric_map(row: dict) -> dict:
    return {m['slug']: m['value'] for m in row.get('metrics', []) if m.get('value') is not None}


def telegram_settings(cfg: dict) -> tuple[str, str, str]:
    env = Config(RepositoryEnv(cfg['telegram_env']))
    owner_env = Config(RepositoryEnv(cfg.get('telegram_owner_env', cfg['telegram_env'])))
    token = env(cfg.get('telegram_token_key', 'TELEGRAM_BOT_TOKEN'))
    expected = cfg.get('telegram_bot_id')
    if expected and token.partition(':')[0] != str(expected):
        raise ValueError('Telegram bot ID mismatch; refusing delivery')
    owner = str(owner_env('TELEGRAM_OWNER_CHAT_ID'))
    if not owner.isdigit() or int(owner) <= 0:
        raise ValueError('Expected existing private owner Telegram chat')
    base = owner_env('TELEGRAM_API_URL', default='https://api.telegram.org').rstrip('/')
    return token, owner, base


def spending_total(data: dict) -> float:
    # The spendings endpoint uses RUBLES, unlike the item analytics cost metrics.
    rows = data['result']['groupings']
    return sum(float(s['value']) for row in rows for s in row['spendings'])


class Monitor:
    def __init__(self, config: dict, client: AvitoClient, *, notify: bool = False):
        self.cfg, self.client, self.notify = config, client, notify
        self.path = Path(config['state_dir']) / 'monitor.json'
        self.state = _read_json(self.path, {'version': 1, 'seen': {}, 'outbox': {}, 'cache': {}})
        for key in ('seen', 'outbox', 'cache'):
            self.state.setdefault(key, {})
        self.now = datetime.now(timezone.utc)
        self.uid = int(config['account_id'])
        self.failed = []

    def save(self):
        self.state['updated_at'] = self.now.isoformat()
        private_json(self.path, self.state)

    def get(self, method: str, path: str, **kwargs) -> dict:
        response = self.client._request(method, path, headers=self.client._auth(), **kwargs)
        response.raise_for_status()
        data = response.json()
        error = data.get('error')
        if error and not (isinstance(error, dict) and error.get('code') == 0):
            raise ValueError('API application error')
        return data

    def event(self, key: str, value, text: str):
        digest = signature(value)
        if self.state['seen'].get(key) == digest:
            return
        self.state['seen'][key] = digest
        self.state['outbox'][key + ':' + digest] = {'text': text, 'created_at': self.now.isoformat()}

    def condition(self, key: str, bad: bool, text: str):
        old = self.state.setdefault('conditions', {}).get(key, False)
        self.state['conditions'][key] = bad
        if bad and not old:
            self.event(key, self.now.isoformat(), text)
        elif old and not bad:
            self.event(key + ':recovered', self.now.isoformat(), 'Авито: восстановлено — ' + key)

    def section(self, name, action):
        try:
            action()
        except Exception as exc:
            self.failed.append(name)
            self.condition('ошибка ' + name, True, f'Авито: проверка «{name}» не выполнена ({error_label(exc)}). Данные не считаем нулевыми. Повтор при следующем запуске.')
            print(f'{name}: {error_label(exc)}', flush=True)
        else:
            self.condition('ошибка ' + name, False, '')
            self.state.setdefault('success_at', {})[name] = self.now.isoformat()
            print(f'{name}: ok', flush=True)
        self.save()

    def chats(self):
        found = []
        for offset in range(0, 10000, 100):
            rows = self.get('GET', f'/messenger/v2/accounts/{self.uid}/chats', params={
                'limit': 100, 'offset': offset, 'unread_only': 'true', 'chat_types': 'u2i,u2u',
            })['chats']
            found.extend(rows)
            if len(rows) < 100:
                break
            time.sleep(1)
        else:
            raise ValueError('Chat pagination exceeded limit')
        initialized = self.state.get('chats_initialized', False)
        cursors = self.state.setdefault('chat_cursors', {})
        incoming = 0
        for chat in found:
            msg = chat.get('last_message') or {}
            if msg.get('direction') != 'in' or not msg.get('id'):
                continue
            incoming += 1
            chat_id, msg_id = str(chat['id']), str(msg['id'])
            if initialized and cursors.get(chat_id) != msg_id:
                title = ((chat.get('context') or {}).get('value') or {}).get('title') or 'Чат с покупателем'
                self.event('chat:' + chat_id, msg_id,
                           f'Авито: новое непрочитанное обращение\n{title}\nhttps://www.avito.ru/profile/messenger/channel/{chat_id}')
            cursors[chat_id] = msg_id
        if not initialized:
            self.event('chats-baseline', incoming, f'Авито: мониторинг обращений включён. Сейчас непрочитанных чатов с последним входящим сообщением: {incoming}. Старые сообщения повторно не рассылаю.\nhttps://www.avito.ru/profile/messenger')
        self.state['chats_initialized'] = True
        self.state['cache']['unread_chats'] = incoming

    def calls(self):
        last = self.state.get('calls_until')
        start = datetime.fromisoformat(last) - timedelta(hours=2) if last else self.now - timedelta(days=1)
        start = max(start, self.now - timedelta(days=7))
        seen = self.state.setdefault('call_cursors', {})
        for offset in range(0, 10000, 100):
            data = self.get('POST', '/calltracking/v1/getCalls/', json={
                'dateTimeFrom': start.isoformat(), 'dateTimeTo': self.now.isoformat(), 'limit': 100, 'offset': offset,
            })
            rows = data['calls']
            for call in rows:
                cid = str(call['callId'])
                if cid not in seen and last and call.get('talkDuration') == 0:
                    item = call.get('itemId')
                    suffix = f'\nОбъявление № {item}' if item else '\nОбъявление не определено API.'
                    self.event('call:' + cid, cid, 'Авито: звонок без разговора — возможно, пропущенный.\n' + call['callTime'] + suffix + '\nПроверьте журнал звонков в Авито Pro.')
                seen[cid] = call['callTime']
            if len(rows) < 100:
                break
            time.sleep(13)  # API limit: 5 requests/minute.
        else:
            raise ValueError('Calls pagination exceeded limit')
        cutoff = (self.now - timedelta(days=9)).isoformat()
        self.state['call_cursors'] = {k: v for k, v in seen.items() if v >= cutoff}
        self.state['calls_until'] = self.now.isoformat()

    def finances(self):
        data = self.get('GET', f'/core/v1/accounts/{self.uid}/balance/')
        real = float(data['real'])
        self.state['cache']['balance'] = {'real': real, 'bonus': data.get('bonus'), 'at': self.now.isoformat()}
        threshold = self.cfg['low_balance_rub']
        self.condition('низкий баланс', real < threshold,
                       f'Авито: в кошельке {real:.2f} ₽, ниже порога {threshold} ₽. Бонусы: {data.get("bonus", "нет данных")}. Автопополнение не включено.')
        today = self.now.astimezone(MSK).date().isoformat()
        spend = self.get('POST', f'/stats/v2/accounts/{self.uid}/spendings', json={
            'dateFrom': today, 'dateTo': today, 'spendingTypes': ['all'], 'grouping': 'day',
        })
        total = spending_total(spend)
        self.state['cache']['spending'] = {'date': today, 'rub': total, 'at': self.now.isoformat()}
        if total >= self.cfg['daily_spending_alert_rub']:
            self.event('spend:' + today, today, f'Авито: расходы за {today} достигли {total:.2f} ₽ (порог {self.cfg["daily_spending_alert_rub"]} ₽). Это уведомление, не запрет расходов. Данные могут поступать с задержкой.')

    def autoload(self):
        current = self.get('GET', '/autoload/v4/uploads/current')
        health = autoload_health(current, current=self.now)
        self.condition('автозагрузка не запускается', health['status'] == 'stale',
                       'Авито: автозагрузка не запускалась более 2 часов. '
                       'Изменения XML и снятие отсутствующих товаров пока не подтверждены на площадке. '
                       'Проверьте расписание и ошибки в кабинете Авито.')
        current_errors = [e for e in current.get('events', []) if e.get('type') in ('error', 'warning')]
        if current_errors or current.get('status') in ('error', 'failed'):
            self.event('current-upload', [current.get('upload_id'), current.get('status'), current_errors],
                       f'Авито: текущая загрузка № {current.get("upload_id")} сообщает проблему ({current.get("status")}). Проверьте журнал автозагрузки.')
        upload = self.get('GET', '/autoload/v4/uploads/last_successful')
        uid = upload['upload_id']
        cache = self.state['cache'].get('autoload', {})
        if cache.get('upload_id') == uid:
            return
        items = self.client.last_successful_items(ad_ids=_feed_items(Path(self.cfg['feed'])))
        mapped = [i for i in items if i.get('ad_id')]
        problems = []
        for it in mapped:
            errors = [m for m in it.get('messages', []) if m.get('type') in ('error', 'warning')]
            if errors or it.get('avito_status') in ('blocked', 'rejected'):
                problems.append({'ad_id': it['ad_id'], 'avito_id': it.get('avito_id'),
                                 'status': it.get('avito_status'), 'messages': errors})
        doc = {'upload_id': uid, 'at': self.now.isoformat(), 'items': mapped, 'problems': problems, 'events': upload.get('events', [])}
        private_json(Path(self.cfg['state_dir']) / 'autoload.json', doc)
        self.state['cache']['autoload'] = {'upload_id': uid, 'at': self.now.isoformat(), 'problems': len(problems)}
        event_errors = [e for e in upload.get('events', []) if e.get('type') in ('error', 'warning')]
        if problems or event_errors:
            titles = _feed_items(Path(self.cfg['feed']))
            details = []
            for p in problems[:8]:
                reasons = '; '.join(str(m.get('title') or m.get('code') or m.get('type', '')) for m in p['messages'])
                details.append(f'{titles.get(p["ad_id"], p["ad_id"])}: {reasons or p["status"]}')
            self.event('upload:' + str(uid), doc, f'Авито: последний завершённый отчёт загрузки № {uid}, начало {upload.get("started_at", "не указано")}.\nОбъявлений с ошибками/предупреждениями: {len(problems)}; событий загрузки: {len(event_errors)}.\n' + '\n'.join(details) + '\nЭто отчёт данного запуска, не текущий статус всех объявлений. Полная диагностика сохранена.')

    def health(self):
        for unit in self.cfg.get('watch_units', []):
            result = subprocess.run(['systemctl', 'show', unit, '--property=Result', '--value'], capture_output=True, text=True, timeout=10)
            value = result.stdout.strip()
            self.condition('служба ' + unit, result.returncode != 0 or value not in ('success', ''),
                           f'Авито: проблема службы {unit}; результат {value or "недоступен"}.')
        observations = _read_json(Path(self.cfg['observations']), {})
        updated = observations.get('updated_at')
        stale = not updated or (self.now - datetime.fromisoformat(updated)).total_seconds() > 1200
        self.condition('контроль снятий', stale, 'Авито: контроль ручных снятий не обновлялся более 20 минут. Проверьте синхронизацию; защита может запаздывать.')
        stopped = _read_json(Path(self.cfg['stop']), {'entries': {}})['entries']
        ids = set(_feed_items(Path(self.cfg['feed'])))
        overlap = sorted(ids & set(stopped))
        self.condition('стоп-лист в XML', bool(overlap), f'Авито: {len(overlap)} остановленных товаров ещё присутствуют в публичном XML. Нужна проверка применения стоп-листа.')
        previous = self.state.get('stop_ids', [])
        added = set(stopped) - set(previous)
        if added:
            self.event('stops', sorted(stopped), f'Авито: запрет повторной выгрузки добавлен для {len(added)} товаров. Всего в стоп-листе: {len(stopped)}. Вернуть их можно отдельным решением; объявления не удаляются навсегда.')
        self.state['stop_ids'] = sorted(stopped)
        self.state['cache']['feed_count'] = len(ids)
        for path in self.cfg.get('stock_snapshots', []):
            snapshot = _read_json(Path(path), {})
            stamp = snapshot.get('generatedAt')
            age = (self.now - datetime.fromisoformat(stamp.replace('Z', '+00:00'))).total_seconds() if stamp else 1e99
            self.condition('остатки ' + Path(path).name, age > 5 * 3600 or age < -300,
                           'Авито: складской снимок I-T-P не обновлён вовремя. Остатки считаем непроверенными, не нулевыми. Автоматический фильтр работает только для явно сопоставленных товаров.')

    def daily(self):
        day = (self.now.astimezone(MSK).date() - timedelta(days=1)).isoformat()
        report_path = Path(self.cfg['state_dir']) / 'reports' / (day + '.json')
        cached = _read_json(report_path, {})
        if cached:
            self.event('daily:' + day, day, cached['summary'])
            return
        rows = []
        for offset in range(0, 100000, 1000):
            payload = self.get('POST', f'/stats/v2/accounts/{self.uid}/items', json={
                'dateFrom': day, 'dateTo': day, 'grouping': 'item', 'metrics': ['impressions', 'views', 'contacts', 'favorites', 'allSpending'],
                'limit': 1000, 'offset': offset,
            })['result']
            batch = payload['groupings']
            rows.extend(batch)
            if len(rows) >= payload['dataTotalCount']:
                break
            if not batch:
                raise ValueError('Incomplete analytics')
            time.sleep(60)  # Official limit: one statistics request/minute.
        else:
            raise ValueError('Analytics pagination exceeded limit')
        titles = _feed_items(Path(self.cfg['feed']))
        observations = _read_json(Path(self.cfg['observations']), {}).get('observations', {})
        links = {str(v.get('avito_id')): k for k, v in observations.items()}
        stopped = set(_read_json(Path(self.cfg['stop']), {'entries': {}})['entries'])
        table = []
        for row in rows:
            aid = links.get(str(row['id']))
            table.append({'avito_id': row['id'], 'feed_id': aid,
                          'title': titles.get(aid, f'Объявление № {row["id"]}'), **metric_map(row)})
        def total(metric):
            if not table or any(metric not in r for r in table):
                return 'нет полных данных'
            val = sum(r[metric] for r in table)
            return f'{val / 100:.2f} ₽' if metric == 'allSpending' else str(val)
        best = sorted(table, key=lambda r: r.get('contacts', -1), reverse=True)[:5]
        suggestions = [r for r in best if r.get('views', 0) >= 30 and r.get('contacts', 0) >= 3
                       and r.get('feed_id') in titles and r.get('feed_id') not in stopped
                       and observations.get(r.get('feed_id'), {}).get('status') == 'active']
        lines = [f'Авито — отчёт за {day} (МСК)', f'В XML: {len(titles)}; запретов повторной выгрузки: {len(stopped)}.',
                 f'Статистика аккаунта: {len(table)} объявлений.', f'Показы: {total("impressions")}; просмотры: {total("views")}; контакты: {total("contacts")}; избранное: {total("favorites")}.',
                 f'Расходы: {total("allSpending")}. Контакты ≠ продажи.', 'Лидеры по контактам:']
        lines += [f'• {r["title"]}: {r.get("views", "н/д")} просмотров / {r.get("contacts", "н/д")} контактов' for r in best]
        lines.append('Кандидаты для ручной оценки продвижения (≥30 просмотров и ≥3 контактов за сутки):')
        lines += [f'• {r["title"]} — сначала подтвердить остаток и маржу.' for r in suggestions] or ['Достаточных сигналов пока нет.']
        bal = self.state['cache'].get('balance', {})
        lines.append(f'Последний известный баланс: {bal.get("real", "н/д")} ₽; проверен: {bal.get("at", "нет данных")}.')
        lines.append('Данные Авито могут уточняться. Платное продвижение автоматически не покупается.')
        summary = '\n'.join(lines)
        private_json(report_path, {'date': day, 'created_at': self.now.isoformat(), 'rows': table, 'summary': summary})
        self.event('daily:' + day, day, summary)

    def flush(self):
        self.save()  # Outbox is durable before any network send.
        if not self.notify:
            print(f'pending_notifications={len(self.state["outbox"])} delivery=disabled')
            return
        token, owner, base = telegram_settings(self.cfg)
        # Only sendMessage. Do not poll getUpdates or interfere with the bot menu.
        with httpx.Client(timeout=30) as http:
            for key, event in list(self.state['outbox'].items()):
                text = event['text']
                parts = [text[i:i + 3500] for i in range(0, len(text), 3500)]
                for index in range(event.get('sent_parts', 0), len(parts)):
                    for attempt in range(3):
                        try:
                            r = http.post(f'{base}/bot{token}/sendMessage', json={
                                'chat_id': owner, 'text': parts[index], 'disable_web_page_preview': True,
                            })
                            break
                        except (httpx.ConnectTimeout, httpx.ConnectError):
                            if attempt == 2:
                                raise
                            time.sleep(3)
                    if r.status_code != 200 or not r.json().get('ok'):
                        raise RuntimeError('Telegram delivery failed; outbox retained')
                    event['sent_parts'] = index + 1
                    receipt = r.json().get('result', {})
                    self.state['last_delivery'] = {'at': datetime.now(timezone.utc).isoformat(),
                                                   'message_id': receipt.get('message_id'),
                                                   'bot_id': (receipt.get('from') or {}).get('id')}
                    self.save()
                del self.state['outbox'][key]
                self.save()
                time.sleep(1)

    def run(self, mode: str):
        try:
            if self.client.get_self_id() != self.uid:
                raise ValueError('Avito account mismatch')
        except Exception as exc:
            self.condition('авторизация API', True, 'Авито: не удалось проверить основной аккаунт (' + error_label(exc) + '). Проверки приостановлены, XML не изменён.')
            self.flush()
            return True
        self.condition('авторизация API', False, '')
        if mode == 'daily':
            self.section('финансы', self.finances)
            self.section('дневной отчёт', self.daily)
        else:
            self.section('обращения', self.chats)
            self.section('звонки', self.calls)
            self.section('автозагрузка', self.autoload)
            self.section('состояние служб', self.health)
            checked = self.state.get('success_at', {}).get('финансы')
            if not checked or (self.now - datetime.fromisoformat(checked)).total_seconds() >= 3600:
                self.section('финансы', self.finances)
        self.flush()
        return bool(self.failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/monitor-main.yaml')
    parser.add_argument('--mode', choices=['poll', 'daily'], default='poll')
    parser.add_argument('--notify', action='store_true')
    args = parser.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text('utf-8'))
    # Systemd flock serializes poll and daily, including all state/outbox writes.
    try:
        with AvitoClient(os.environ['AVITO_CLIENT_ID'], os.environ['AVITO_CLIENT_SECRET'],
                         max_retries=3, backoff_base=2, pagination_delay=3) as client:
            return int(Monitor(cfg, client, notify=args.notify).run(args.mode))
    except Exception as exc:
        print('monitor failed: ' + error_label(exc), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
