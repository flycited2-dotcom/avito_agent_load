import os
import json
from datetime import datetime, timedelta, timezone
from collections import Counter
from avito_bridge.avito.client import AvitoClient

with AvitoClient(os.environ['AVITO_CLIENT_ID'], os.environ['AVITO_CLIENT_SECRET']) as c:
    path='/autoload/v4/uploads/last_successful/items'
    items=[]
    for page in (1,2,3):
        r=c._request('GET',path,headers=c._auth(),params={'page':page,'perPage':100})
        d=r.json(); items.extend(d.get('items',[]))
        print('page', page, 'meta', d.get('meta'), 'count',len(d.get('items',[])), 'keys',list(d))
    counts=Counter(i.get('ad_id') for i in items)
    print('duplicates',json.dumps({k:v for k,v in counts.items() if v>1}))
    print('sections',dict(Counter((i.get('section') or {}).get('slug') for i in items)))
    print('sample',json.dumps(items[:1],ensure_ascii=False))
    for query in ('adab767002158a46ab14850f', 'adab767002158a46ab14850f,52ba88b7bcd858029a6ad0d6'):
        r=c._request('GET',path,headers=c._auth(),params={'query':query,'perPage':100})
        d=r.json(); print('query',query,'meta',d.get('meta'),'ids',[i.get('ad_id') for i in d.get('items',[])])
    now=datetime.now(timezone.utc)
    r=c._request('POST','/calltracking/v1/getCalls/',headers=c._auth(),json={'dateTimeFrom':(now-timedelta(days=1)).isoformat(),'dateTimeTo':now.isoformat(),'limit':100,'offset':0})
    d=r.json();print('calls_http',r.status_code,'error',d.get('error'),'keys',list(d),'count',len(d.get('calls',[])))
