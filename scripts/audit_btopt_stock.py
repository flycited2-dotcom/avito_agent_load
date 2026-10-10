import json
from pathlib import Path
import yaml
from openpyxl import load_workbook
from avito_bridge.ingest.price_xls import parse_price_xls
from avito_bridge.feed.ad_id import make_ad_id
from avito_bridge.avito.manual_stop import _feed_items
from avito_bridge.ingest.btopt_availability import read_availability

cfg=yaml.safe_load(Path('profiles/appliances-btopt.yaml').read_text())
opts=cfg['profile']['source_options']
old=parse_price_xls(Path('runtime/appliances/current.xlsx'),opts)
fresh=Path('/opt/content-factory/state/prices/mail__info_simfer_com_ru_прайс_лист_опт_xlsx.xlsx')
snapshot=read_availability(fresh)
stock=snapshot['items']
feed=_feed_items(Path('/opt/oasis/staticfiles/avito-feed.xml'))
result=[]
for row in old:
    aid=make_ad_id('pricexls:'+row['article'],'simferopol')
    if aid in feed:
        latest=stock.get(row['article'])
        result.append({'ad_id':aid,'article':row['article'],'title':feed[aid], 'old_name':row['name'],
                       'availability':latest, 'present_in_new':bool(latest)})
print('date',snapshot['date'],'supplier_rows',len(stock),'mapped_live',len(result),'missing_unknown',sum(not r['present_in_new'] for r in result))
print(json.dumps([r for r in result if not r['present_in_new']],ensure_ascii=False,indent=2))
