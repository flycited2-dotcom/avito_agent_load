"""Read-only diagnostics; no secrets or customer messages in output."""
import json
import os
from collections import Counter
from pathlib import Path
from lxml import etree
from decouple import Config, RepositoryEnv
from avito_bridge.avito.client import AvitoClient

feed = etree.parse('/opt/oasis/staticfiles/avito-feed.xml').getroot()
ads = {a.findtext('Id'): a.findtext('Title') for a in feed}
print('feed_count', len(ads))
print('microwaves', json.dumps({k: v for k,v in ads.items() if 'микроволн' in (v or '').lower()}, ensure_ascii=False))
snapshot = json.loads(Path('/opt/avito-bridge/runtime/itp-crimea/latest.json').read_text())
print('itp_generated', snapshot.get('generatedAt'), 'count', len(snapshot.get('items', [])))
print('itp_microwaves', json.dumps([{k:v for k,v in i.items() if k in ('sku','name','stock','avitoPrice')} for i in snapshot['items'] if 'микроволн' in str(i.get('name', '')).lower()], ensure_ascii=False))
env = Config(RepositoryEnv('/opt/splithub_api_telegram/.env'))
print('telegram_config', bool(env('TELEGRAM_BOT_TOKEN', default='')), 'owner_private', str(env('TELEGRAM_OWNER_CHAT_ID', default='')).isdigit())
with AvitoClient(os.environ['AVITO_CLIENT_ID'], os.environ['AVITO_CLIENT_SECRET'], max_retries=2) as c:
    print('account', c.get_self_id())
    for status in ('active', 'removed', 'old', 'blocked', 'rejected'):
        r=c._request('GET','/core/v1/items',headers=c._auth(),params={'status':status,'per_page':99,'page':1})
        d=r.json()
        rows=d.get('resources', [])
        print('query_status',status,'http',r.status_code,'meta',d.get('meta'),'returned_status',dict(Counter(i.get('status') for i in rows)))
        print('fields', list(rows[0]) if rows else [])
        print('microwaves_status',json.dumps([{k:v for k,v in i.items() if k in ('id','title','status')} for i in rows if 'микроволн' in str(i.get('title','')).lower()],ensure_ascii=False))
    for endpoint in ('current','last_successful'):
        r=c._request('GET','/autoload/v4/uploads/'+endpoint,headers=c._auth())
        d=r.json()
        print('upload',endpoint,r.status_code,json.dumps({k:v for k,v in d.items() if k in ('upload_id','status','started_at','events','stats')},ensure_ascii=False))
