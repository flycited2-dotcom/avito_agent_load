"""Send one explicitly requested setup test to the existing stock-bot owner."""
import httpx
import sys
from decouple import Config, RepositoryEnv

env = Config(RepositoryEnv('/opt/splithub_api_telegram/.env'))
token = env('TELEGRAM_BOT_TOKEN')
owner = env('TELEGRAM_OWNER_CHAT_ID')
base = env('TELEGRAM_API_URL', default='https://api.telegram.org').rstrip('/')
try:
    with httpx.Client(timeout=30) as http:
        me = http.get(f'{base}/bot{token}/getMe').json()
        chat = http.post(f'{base}/bot{token}/getChat', json={'chat_id': owner}).json()
        if not me.get('ok') or not chat.get('ok') or chat['result'].get('type') != 'private':
            raise ValueError('Bot or private owner chat could not be verified')
        print('bot=@' + me['result']['username'], 'bot_id=' + str(me['result']['id']), 'chat_type=private', flush=True)
        if '--inspect-only' in sys.argv:
            raise SystemExit(0)
        response = http.post(f'{base}/bot{token}/sendMessage', json={
            'chat_id': owner,
            'text': 'Проверка связи Авито → ваш бот складских отчётов. Это тестовое сообщение. Сейчас исправляю повторное включение снятых объявлений и привязку к остаткам IT-партнёров, склад Симферополь. Итоговый запуск подтвержу отдельно.',
        })
        data = response.json()
        print('delivery_http=', response.status_code, 'ok=', data.get('ok'), 'message_id=', data.get('result', {}).get('message_id'))
except Exception as exc:
    print('notification_test_failed', type(exc).__name__)
    raise SystemExit(1)
