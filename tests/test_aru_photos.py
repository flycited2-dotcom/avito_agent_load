from copy import deepcopy
from datetime import datetime,timezone
import json
from xml.etree import ElementTree as ET

import pytest
from avito_bridge.config import load_config
from avito_bridge.ingest.aru_snapshot import fetch_aru_snapshot
from avito_bridge.orchestrator.pipeline import run_cycle

BASE='https://aru.ooo/wa-data/public/shop/products/'

def config(tmp_path,images):
    rows=[{'id':'42','name':'Дрель АРУ','available':True,'price':'100.01','price_basis':'account_price','image_urls':images}]
    other=deepcopy(rows[0]);other.update(id='43',name='Лобзик АРУ');rows.append(other)
    p=tmp_path/'catalog.json';p.write_text(json.dumps({'schema_version':1,'source':'aru','complete':True,'generated_at':datetime.now(timezone.utc).isoformat(),'items':rows}),encoding='utf-8')
    cfg=load_config('profiles/aru.yaml');cfg.source_options['path']=str(p)
    return cfg

@pytest.mark.parametrize('thumb',[
    BASE+'00/webp/81/14/51481/images/37577/37577.180.webp',
    BASE+'81/14/51481/images/37577/37577.180.jpg',
])
def test_main_photo_is_full_size_same_supplier_image(tmp_path,thumb):
    cfg=config(tmp_path,[thumb])
    original=BASE+'81/14/51481/images/37577/37577.970.jpg'
    assert fetch_aru_snapshot(cfg)[0].photos==[original]

def test_photo_order_deduplicates_thumbnail_and_original(tmp_path):
    first=BASE+'81/14/51481/images/37577/37577.970.jpg'
    second=BASE+'81/14/51481/images/37578/37578.970.jpg'
    cfg=config(tmp_path,[first.replace('.970.','.180.'),first,second])
    assert fetch_aru_snapshot(cfg)[0].photos==[first,second]

def test_photo_normalization_does_not_invent_unknown_paths_or_fetch_network(tmp_path,monkeypatch):
    import httpx
    def forbidden(*args,**kwargs):raise AssertionError('Photo loader must be offline')
    monkeypatch.setattr(httpx,'get',forbidden)
    cfg=config(tmp_path,['https://aru.ooo/photo.jpg'])
    assert fetch_aru_snapshot(cfg)[0].photos==['https://aru.ooo/photo.jpg']

def test_selected_only_xml_uses_supplier_original_without_generated_cards(tmp_path):
    cfg=config(tmp_path,[BASE+'00/webp/81/14/51481/images/37577/37577.180.webp'])
    cfg.selected_series=frozenset({'aru|item|aru:42'})
    cfg.source_options.update(prices_approved=True,product_tags={'42':{'Category':'Ремонт и строительство'}})
    assert cfg.cards.enabled is False
    target=tmp_path/'preview.xml'
    result=run_cycle(lambda:fetch_aru_snapshot(cfg),cfg,target,tmp_path/'preview.db')
    ads=ET.parse(target).getroot().findall('Ad')
    assert result.ads_built==1 and len(ads)==1
    assert ads[0].find('Images/Image').get('url')==BASE+'81/14/51481/images/37577/37577.970.jpg'
    assert ads[0].findtext('Price')=='111'
