from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from avito_bridge.ingest.aru_categories import build_category_snapshot, validate_category_coverage


ROOT = '/elektroinstrument/'
CHILD = ROOT + 'lobziki/'
ORDER = ROOT + 'pod-zakaz/'
NOW = datetime.now(timezone.utc)


def category(identity, name):
    return {'id': identity, 'name': name, 'url': 'https://aru.ooo' + identity}


def product(identity='42', path=CHILD, **changes):
    row = {'id': identity, 'name': 'Лобзик электрический', 'article': 'ST1000',
           'url': 'https://aru.ooo' + path + 'lobzik/', 'stock_text': 'В наличии',
           'price_text': '43,48 ₽', 'image_urls': ['https://aru.ooo/lobzik.jpg']}
    row.update(changes)
    return row


def page(identity, items=None, children=None, **changes):
    row = {'category_id': identity, 'page': 1, 'pages': 1,
           'url': 'https://aru.ooo' + identity, 'authenticated': True,
           'captured_at': NOW.isoformat(), 'title': '', 'categories': [],
           'children': children or [], 'items': items or []}
    row.update(changes)
    return row


def menu():
    return [category(ROOT, 'Электроинструмент'), category(CHILD, 'Лобзики')]


def captures():
    return [page(ROOT, children=[category(CHILD, 'Лобзики')]), page(CHILD, [product()])]


def test_builds_named_hierarchy_and_prices_once():
    data = build_category_snapshot(captures(), menu())
    item = data['items'][0]
    assert item['category_path'] == ['Электроинструмент', 'Лобзики']
    assert item['category_ids'] == [ROOT, CHILD]
    assert item['category_slugs'] == ['elektroinstrument', 'lobziki']
    assert item['price'] == '43.48'
    assert item['content_price_rub'] == 48
    assert item['content_price'] == '47.83'
    assert data['completeness_basis'] == 'category_tree'
    assert data['categories'][1]['parent_id'] == ROOT
    validate_category_coverage(data)


def test_parent_products_do_not_replace_required_child_scan():
    pages = captures()
    pages[0]['items'] = [product('1', ROOT)]
    result = build_category_snapshot(pages, menu())
    assert {r['id'] for r in result['items']} == {'1', '42'}
    with pytest.raises(ValueError, match='missing category coverage'):
        build_category_snapshot(pages[:1], menu())


def test_discovered_child_outside_initial_menu_is_required():
    with pytest.raises(ValueError, match='missing category coverage'):
        build_category_snapshot(captures()[:1], menu()[:1])
    assert len(build_category_snapshot(captures(), menu()[:1])['items']) == 1


def test_product_canonical_path_outside_manifest_requires_visit():
    pages = captures()
    pages[1]['items'] = [product(path=ROOT + 'perforatory/')]
    with pytest.raises(ValueError, match='/elektroinstrument/perforatory/'):
        build_category_snapshot(pages, menu())


def test_latest_stock_observation_wins_before_filtering():
    pages = captures()
    pages[0]['items'] = [product(stock_text='Нет в наличии')]
    pages[0]['captured_at'] = (NOW + timedelta(seconds=1)).isoformat()
    data = build_category_snapshot(pages, menu())
    assert data['items'] == []
    assert data['scanned_products'] == 1
    assert data['excluded_products'] == 1
    assert data['category_scan']['observations'] == 2
    assert data['category_scan']['duplicates_reconciled'] == 1


def test_on_order_category_excluded_even_when_card_claims_in_stock():
    pages = [page(ROOT), page(ORDER, [product(path=ORDER)])]
    data = build_category_snapshot(pages, [category(ROOT, 'Электроинструмент'),
                                          category(ORDER, 'Под заказ')])
    assert data['items'] == []
    assert data['excluded_by_reason'] == {'on_order_category': 1}


@pytest.mark.parametrize('changes', [
    {'stock_text': 'Нет в наличии'}, {'stock_text': 'Под заказ'},
    {'stock_text': ''}, {'price_text': '0 ₽'}, {'price_text': '-10 ₽'},
    {'price_text': 'Цена по запросу'},
])
def test_only_explicit_stock_and_positive_account_price(changes):
    pages = captures()
    pages[1]['items'] = [product(**changes)]
    assert build_category_snapshot(pages, menu())['items'] == []


def test_pagination_coverage_and_actual_url_are_checked_per_category():
    pages = captures()
    pages[1]['pages'] = 2
    with pytest.raises(ValueError, match='incomplete page coverage'):
        build_category_snapshot(pages, menu())
    pages.append(page(CHILD, [product('43')], page=2, pages=2,
                      url='https://aru.ooo' + CHILD + '?page=2'))
    assert len(build_category_snapshot(pages, menu())['items']) == 2
    pages[2]['url'] = 'https://aru.ooo' + CHILD
    with pytest.raises(ValueError, match='invalid page'):
        build_category_snapshot(pages, menu())


@pytest.mark.parametrize('empty_number', [1, 2, 3])
def test_empty_page_in_paginated_category_cannot_certify_complete_scan(empty_number):
    pages = [page(ROOT)]
    for number in range(1, 4):
        pages.append(page(CHILD, [] if number == empty_number else [product(str(number))],
                          page=number, pages=3,
                          url='https://aru.ooo' + CHILD + f'?page={number}'))
    # URL, authentication and page-number coverage all look valid; no cards
    # indicates an unreadable/unsupported catalogue view, not a complete scan.
    with pytest.raises(ValueError, match='empty paginated category page'):
        build_category_snapshot(pages, menu())


@pytest.mark.parametrize('changes', [
    {'authenticated': False}, {'captured_at': (NOW - timedelta(days=2)).isoformat()},
    {'captured_at': '2026-10-10T12:00:00'}, {'url': 'https://evil.example/elektroinstrument/'},
])
def test_capture_integrity(changes):
    pages = captures()
    pages[0].update(changes)
    with pytest.raises(ValueError):
        build_category_snapshot(pages, menu())


def test_valid_empty_category_remains_in_manifest():
    result = build_category_snapshot([page(ROOT)], menu()[:1])
    assert result['items'] == []
    assert result['category_scan']['captured_categories'] == 1
    validate_category_coverage(result)


def test_duplicate_card_on_same_page_rejected():
    pages = captures()
    pages[1]['items'].append(deepcopy(pages[1]['items'][0]))
    with pytest.raises(ValueError, match='duplicate or invalid product'):
        build_category_snapshot(pages, menu())


def test_server_rejects_truncated_persisted_manifest():
    result = build_category_snapshot(captures(), menu())
    result['category_scan']['categories'].pop()
    with pytest.raises(ValueError, match='inconsistent category manifest'):
        validate_category_coverage(result)


def test_generated_at_preserves_oldest_capture():
    pages = captures()
    pages[0]['captured_at'] = (NOW - timedelta(hours=1)).isoformat()
    assert build_category_snapshot(pages, menu())['generated_at'] == pages[0]['captured_at']


def test_explicit_aggregate_views_supplement_tree_without_reclassifying_products():
    pages = captures() + [page('/vse-tovary/', [product('43', '/vse-tovary/'), product()])]
    categories = menu() + [category('/vse-tovary/', 'Все товары')]
    data = build_category_snapshot(pages, categories)
    by_id = {item['id']: item for item in data['items']}
    assert by_id['42']['category_path'] == ['Электроинструмент', 'Лобзики']
    assert by_id['43']['category_path'] == ['Прочие товары']
    validate_category_coverage(data)
