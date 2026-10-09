from datetime import datetime, timezone
import json
import pytest
from avito_bridge.config import load_config
from avito_bridge.ingest.aru_snapshot import fetch_aru_snapshot


def config(tmp_path, payload):
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    cfg = load_config('profiles/aru.yaml')
    cfg.source_options['path'] = str(path)
    return cfg


def payload():
    return {'schema_version': 1, 'source': 'aru', 'complete': True,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'items': [{'id': '42', 'article': 'DR1', 'name': 'Дрель DCK',
                       'brand': 'DCK', 'available': True, 'price': '1200',
                       'price_basis': 'account_price',
                       'category_path': ['tools'], 'specifications': {},
                       'image_urls': ['https://aru.ooo/photo.jpg'],
                       'url': 'https://aru.ooo/tools/drill/'}]}


def test_preview_preserves_products_but_does_not_approve_price(tmp_path):
    offer = fetch_aru_snapshot(config(tmp_path, payload()))[0]
    assert offer.supplier_sku == 'aru:42'
    assert offer.attrs['meta:supplier_article'] == 'DR1'
    assert offer.retail_ref == 1200
    assert offer.price_override is None
    assert offer.cost is None


def test_partial_rejected_but_last_successful_snapshot_does_not_expire(tmp_path):
    data = payload()
    data['complete'] = False
    with pytest.raises(ValueError, match='complete'):
        fetch_aru_snapshot(config(tmp_path, data))
    data['complete'] = True
    data['generated_at'] = '2000-01-01T00:00:00+00:00'
    assert len(fetch_aru_snapshot(config(tmp_path, data))) == 1
    data['generated_at'] = '2100-01-01T00:00:00+00:00'
    with pytest.raises(ValueError, match='future'):
        fetch_aru_snapshot(config(tmp_path, data))


def test_price_requires_explicit_selected_product_and_taxonomy(tmp_path):
    cfg = config(tmp_path, payload())
    cfg.source_options['prices_approved'] = True
    assert fetch_aru_snapshot(cfg)[0].price_override is None
    cfg.source_options['selected_product_ids'] = ['42']
    cfg.source_options['product_tags'] = {'42': {'Category': 'Ремонт и строительство'}}
    assert fetch_aru_snapshot(cfg)[0].price_override == 1320


@pytest.mark.parametrize('changes', [
    {'available': False}, {'available': None}, {'price': None},
    {'price_basis': 'site_price'}, {'price_basis': 'hidden'},
])
def test_only_in_stock_account_prices_enter_catalogue(tmp_path, changes):
    data = payload()
    data['items'][0].update(changes)
    assert fetch_aru_snapshot(config(tmp_path, data)) == []


def test_markup_uses_kopeks_before_rounding(tmp_path):
    data = payload()
    data['items'][0]['price'] = '43.48'
    offer = fetch_aru_snapshot(config(tmp_path, data))[0]
    assert offer.attrs['meta:account_price'] == '43.48'
    assert offer.attrs['meta:content_price'] == '48'
    assert offer.price_override is None


def test_catalogue_exposes_price_preview_without_publication_approval(tmp_path):
    from avito_bridge.catalog_export import build_catalog_json
    cfg = config(tmp_path, payload())
    data = build_catalog_json(fetch_aru_snapshot(cfg), cfg)
    member = data['series'][0]['members'][0]
    assert member['account_price'] == '1200'
    assert member['content_price_preview'] == 1320
    assert member['price_ok'] is False
    assert member['supplier_article'] == 'DR1'
    assert member['supplier_updated_at']
