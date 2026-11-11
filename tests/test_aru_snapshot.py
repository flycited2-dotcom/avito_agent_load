from datetime import datetime, timezone
from copy import deepcopy
import json
from pathlib import Path
import pytest
import yaml
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


def test_category_tree_reaches_export_groups_and_members(tmp_path):
    from avito_bridge.catalog_export import build_catalog_json
    data = payload()
    data['items'][0].update(category_path=['Электроинструмент', 'Дрели'],
                            category_ids=['/elektroinstrument/', '/elektroinstrument/dreli/'])
    cfg = config(tmp_path, data)
    group = build_catalog_json(fetch_aru_snapshot(cfg), cfg)['series'][0]
    assert group['category_path'] == ['Электроинструмент', 'Дрели']
    assert group['category_ids'] == data['items'][0]['category_ids']
    assert group['members'][0]['category_path'] == group['category_path']


def test_on_order_url_never_enters_catalogue(tmp_path):
    data = payload()
    data['items'][0]['url'] = 'https://aru.ooo/elektroinstrument/pod-zakaz/drill/'
    assert fetch_aru_snapshot(config(tmp_path, data)) == []


def selection_config(tmp_path):
    data = payload()
    second = deepcopy(data['items'][0])
    second.update(id='43', name='Лобзик Procraft', article='ST43')
    data['items'].append(second)
    cfg = config(tmp_path, data)
    cfg.source_options.update(prices_approved=True, selected_product_ids=['43'],
                              product_tags={str(identity): {'Category': 'Ремонт и строительство'}
                                            for identity in (42, 43)})
    return cfg


@pytest.mark.parametrize('selection,expected', [
    ([], {'42', '43'}), (['aru|item|aru:42'], {'42'}),
    (['aru|item|aru:43'], {'43'}), (['__none__'], set()),
])
def test_studio_selection_is_authoritative_over_legacy_id_list(tmp_path, selection, expected):
    cfg = selection_config(tmp_path)
    cfg.selected_series = frozenset(selection)
    approved = {offer.supplier_sku.split(':')[1] for offer in fetch_aru_snapshot(cfg)
                if offer.price_override is not None}
    assert approved == expected


@pytest.mark.parametrize('legacy,expected', [(['43'], {'43'}), ([], set())])
def test_legacy_selection_only_applies_to_profile_without_selected_series(tmp_path, legacy, expected):
    cfg = selection_config(tmp_path)
    document = yaml.safe_load(Path('profiles/aru.yaml').read_text(encoding='utf-8'))
    document['profile']['source_options'] = cfg.source_options
    document['profile']['source_options']['selected_product_ids'] = legacy
    document['catalog'].pop('selected_series')
    path = tmp_path / 'aru.yaml'
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding='utf-8')
    cfg = load_config(path)
    assert not cfg.selected_series_explicit
    approved = {offer.supplier_sku.split(':')[1] for offer in fetch_aru_snapshot(cfg)
                if offer.price_override is not None}
    assert approved == expected
    # Studio's next explicit save overrides the migration fallback, including all.
    document['catalog']['selected_series'] = []
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding='utf-8')
    cfg = load_config(path)
    assert cfg.selected_series_explicit
    assert all(offer.price_override is not None for offer in fetch_aru_snapshot(cfg))


def test_studio_selection_reaches_built_feed_without_second_whitelist(tmp_path):
    from avito_bridge.feed.ad_id import make_ad_id
    from avito_bridge.orchestrator.pipeline import run_cycle
    cfg = selection_config(tmp_path)
    cfg.selected_series = frozenset({'aru|item|aru:42'})
    feed = tmp_path / 'aru-preview.xml'
    result = run_cycle(lambda: fetch_aru_snapshot(cfg), cfg, feed, tmp_path / 'state.db')
    xml = feed.read_text(encoding='utf-8')
    assert result.ads_built == 1
    assert make_ad_id('aru:42', 'simferopol') in xml
    assert make_ad_id('aru:43', 'simferopol') not in xml


def test_selection_does_not_bypass_price_or_taxonomy_approval(tmp_path):
    cfg = selection_config(tmp_path)
    cfg.selected_series = frozenset({'aru|item|aru:42'})
    cfg.source_options['prices_approved'] = False
    assert all(offer.price_override is None for offer in fetch_aru_snapshot(cfg))
    cfg.source_options['prices_approved'] = True
    cfg.source_options['product_tags'] = {}
    assert all(offer.price_override is None for offer in fetch_aru_snapshot(cfg))


def test_no_selection_preserves_feed_minimum_and_publication_gate(tmp_path):
    from avito_bridge.orchestrator.pipeline import run_cycle
    from avito_bridge.profile_publish import validate_publish_target
    cfg = selection_config(tmp_path)
    cfg.selected_series = frozenset({'__none__'})
    feed = tmp_path / 'aru-preview.xml'
    previous = b'<Ads />'
    feed.write_bytes(previous)
    with pytest.raises(ValueError, match='минимум профиля'):
        run_cycle(lambda: fetch_aru_snapshot(cfg), cfg, feed, tmp_path / 'state.db')
    assert feed.read_bytes() == previous
    with pytest.raises(ValueError, match='not allowed for publication'):
        validate_publish_target('profiles/aru.yaml', cfg, tmp_path)
