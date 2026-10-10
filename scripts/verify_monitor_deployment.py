import hashlib
import json
from pathlib import Path
from collections import Counter
from lxml import etree

root=Path('/opt/avito-bridge')
feed=Path('/opt/oasis/staticfiles/avito-feed.xml')
ads=etree.parse(str(feed)).getroot().findall('Ad')
ids={a.findtext('Id') for a in ads}
state=json.loads((root/'state/monitor-main/monitor.json').read_text())
obs=json.loads((root/'state/avito-status-main.json').read_text())
stop=json.loads((root/'state/manual-stop-main.json').read_text())
print('feed_count',len(ads),'sha256',hashlib.sha256(feed.read_bytes()).hexdigest())
print('pending_notifications',len(state['outbox']),'success_at',json.dumps(state.get('success_at'),ensure_ascii=False))
print('api_observation',obs['updated_at'],'coverage',sum(k in ids for k in obs['observations']))
print('current_feed_observed_statuses',dict(Counter(v.get('status') for k,v in obs['observations'].items() if k in ids)))
print('inactive_in_feed',json.dumps([{k:v for k,v in o.items() if k in ('ad_id','avito_id','title','status')} for i,o in obs['observations'].items() if i in ids and o.get('status') not in ('active',)],ensure_ascii=False))
print('stop_count',len(stop['entries']),'stopped_in_feed',len(ids & set(stop['entries'])))
print('microwaves',sum('микроволн' in (a.findtext('Title') or '').lower() for a in ads))
for aid in ('4c65b6726055bbac8a8f1985','e9d52fba8ea7698c31c38834'):
    matches=[a for a in ads if a.findtext('Id')==aid]
    print('rucelf',aid,'images',len(matches[0].findall('Images/Image')) if matches else 'absent')
print('report_files',sorted(p.name for p in (root/'state/monitor-main/reports').glob('*.json')))
