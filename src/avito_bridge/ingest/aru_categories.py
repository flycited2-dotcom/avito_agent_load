"""Compile authenticated ARU category captures with verifiable tree coverage.

The supplier's /vse-tovary/ view is not a complete catalogue. Completeness here
means every menu category, discovered child and canonical product category was
visited, including categories that contain both products and children.
"""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
import re
from urllib.parse import parse_qs, urlparse


VIEWS = {'/vse-tovary/', '/novinki/'}


def category_ids(path):
    """Stable full-prefix category identifiers, never translated display names."""
    parts = path.strip('/').split('/') if path.strip('/') else []
    return ['/' + '/'.join(parts[:index]) + '/' for index in range(1, len(parts) + 1)]


def _category_id(value):
    parsed = urlparse(str(value))
    if parsed.scheme and (parsed.scheme != 'https' or parsed.netloc != 'aru.ooo'):
        raise ValueError('aru_categories: invalid category host')
    path = parsed.path
    if (not path.startswith('/') or not path.endswith('/') or path == '/'
            or '//' in path or any(p in {'.', '..'} for p in path.split('/'))
            or parsed.query or parsed.fragment):
        raise ValueError('aru_categories: invalid category path')
    return path


def _price(value):
    cleaned = re.sub(r'[\s\u00a0\u202f]', '', str(value)).replace(',', '.')
    match = re.fullmatch(r'(\d+(?:\.\d{1,2})?)(?:₽|руб\.?|р\.?)?', cleaned, re.I)
    if not match:
        return None
    try:
        price = Decimal(match.group(1))
    except InvalidOperation:
        return None
    return price if price.is_finite() and price > 0 else None


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError) as error:
        raise ValueError('aru_categories: invalid capture timestamp') from error
    if stamp.tzinfo is None:
        raise ValueError('aru_categories: timezone missing')
    if not -300 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 86400:
        raise ValueError('aru_categories: stale capture')
    return stamp


def build_category_snapshot(pages, categories):
    if not pages or not categories:
        raise ValueError('aru_categories: pages and category menu required')
    names, required = {}, set()
    included_views = {_category_id(entry['id']) for entry in categories} & VIEWS

    def register(entries):
        for entry in entries:
            identity = _category_id(entry['id'])
            if identity in VIEWS and identity not in included_views:
                continue
            if entry.get('url') and _category_id(entry['url']) != identity:
                raise ValueError('aru_categories: category URL mismatch')
            required.update(category_ids(identity))
            name = str(entry.get('name') or '').strip()
            if identity == '/vse-tovary/':
                name = 'Прочие товары'
            if name:
                names.setdefault(identity, name)

    register(categories)
    for page in pages:
        register(page.get('categories') or [])
        register(page.get('children') or [])
        identity = _category_id(page['category_id'])
        if identity in VIEWS and identity not in included_views:
            raise ValueError('aru_categories: aggregate view not included in category menu')
        required.update(category_ids(identity))
        if page.get('title'):
            names.setdefault(identity, str(page['title']).strip())

    visited, observations, stamps, coverage, total_observations = {}, {}, [], {}, 0
    for page in pages:
        identity = _category_id(page['category_id'])
        url = urlparse(page['url'])
        try:
            actual_page = int(parse_qs(url.query).get('page', ['1'])[0])
        except ValueError as error:
            raise ValueError('aru_categories: invalid page URL') from error
        number, total = page['page'], page['pages']
        if (type(number) is not int or type(total) is not int
                or not 1 <= number <= total <= 1000
                or page.get('authenticated') is not True
                or url.scheme != 'https' or url.netloc != 'aru.ooo'
                or url.path != identity or actual_page != number
                or not isinstance(page.get('items'), list)):
            raise ValueError('aru_categories: unauthenticated or invalid page')
        if total > 1 and not page['items']:
            raise ValueError('aru_categories: empty paginated category page: '
                             + identity + f' page {number}/{total}')
        stamp = _stamp(page['captured_at'])
        stamps.append(stamp)
        numbers = visited.setdefault(identity, set())
        if number in numbers:
            raise ValueError('aru_categories: duplicate category page')
        numbers.add(number)
        scan = coverage.setdefault(identity, {'id': identity, 'pages': total,
                                               'captured_pages': [], 'observations': 0})
        if scan['pages'] != total:
            raise ValueError('aru_categories: changing pagination')
        scan['captured_pages'].append(number)
        scan['observations'] += len(page['items'])
        page_ids = set()
        for row in page['items']:
            product_id = str(row.get('id', ''))
            product_url = urlparse(row.get('url', ''))
            if (not product_id.isdigit() or product_id in page_ids or not row.get('name')
                    or product_url.scheme != 'https' or product_url.netloc != 'aru.ooo'
                    or not product_url.path.strip('/')):
                raise ValueError('aru_categories: duplicate or invalid product')
            page_ids.add(product_id)
            total_observations += 1
            product_path = '/' + '/'.join(product_url.path.strip('/').split('/')[:-1]) + '/'
            if product_path == '//':
                raise ValueError('aru_categories: product category missing')
            required.update(category_ids(product_path))
            previous = observations.get(product_id)
            if previous is None or stamp >= previous[1]:
                observations[product_id] = (row, stamp, product_path)

    missing = sorted(required - visited.keys())
    if missing:
        raise ValueError('aru_categories: missing category coverage: ' + ', '.join(missing))
    for identity, numbers in visited.items():
        if numbers != set(range(1, coverage[identity]['pages'] + 1)):
            raise ValueError('aru_categories: incomplete page coverage: ' + identity)
        coverage[identity]['captured_pages'].sort()
    unnamed = sorted(required - names.keys())
    if unnamed:
        raise ValueError('aru_categories: category names missing: ' + ', '.join(unnamed))

    tree = []
    for identity in sorted(required):
        path_ids = category_ids(identity)
        tree.append({'id': identity, 'name': names[identity],
                     'parent_id': path_ids[-2] if len(path_ids) > 1 else None,
                     'url': 'https://aru.ooo' + identity, 'path_ids': path_ids,
                     'path_names': [names[path] for path in path_ids]})
    items, excluded = [], Counter()
    for identity, (row, stamp, path) in sorted(observations.items(), key=lambda pair: int(pair[0])):
        slugs = path.strip('/').split('/')
        if 'pod-zakaz' in slugs:
            excluded['on_order_category'] += 1
            continue
        if str(row.get('stock_text') or '').strip().casefold() != 'в наличии':
            excluded['not_in_stock'] += 1
            continue
        price = _price(row.get('price_text', ''))
        if price is None:
            excluded['unpriced'] += 1
            continue
        sale = price * Decimal('1.10')
        ids = category_ids(path)
        items.append({'id': identity, 'article': row.get('article', ''), 'name': row['name'],
                      'url': row['url'], 'brand': row.get('brand', ''),
                      'category_path': [names[p] for p in ids], 'category_ids': ids,
                      'category_slugs': slugs, 'available': True, 'stock_text': 'В наличии',
                      'price': str(price), 'price_basis': 'account_price',
                      'content_markup_pct': '10',
                      'content_price': str(sale.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
                      'content_price_rub': int(sale.quantize(Decimal('1'), rounding=ROUND_CEILING)),
                      'captured_at': stamp.isoformat(), 'specifications': row.get('specifications', {}),
                      'image_urls': row.get('image_urls', []), 'description': row.get('description', '')})
    return {'schema_version': 1, 'source': 'aru', 'complete': True, 'authenticated': True,
            'price_basis': 'account_price', 'completeness_basis': 'category_tree',
            'generated_at': min(stamps).isoformat(), 'pages': len(pages),
            'captured_pages': len(pages), 'scanned_products': len(observations),
            'excluded_products': sum(excluded.values()), 'excluded_by_reason': dict(excluded),
            'content_markup_pct': '10', 'categories': tree,
            'category_scan': {'required_categories': len(required), 'captured_categories': len(visited),
                              'observations': total_observations, 'unique_products': len(observations),
                              'duplicates_reconciled': total_observations - len(observations),
                              'missing_categories': [], 'categories': list(coverage.values())},
            'items': items}


def validate_category_coverage(data):
    """Verify persisted coverage before installation; old snapshots use another basis."""
    if data.get('completeness_basis') != 'category_tree':
        raise ValueError('aru_categories: category_tree completeness required')
    tree = data.get('categories') or []
    ids = {_category_id(row['id']) for row in tree}
    scans = data.get('category_scan') or {}
    coverage = scans.get('categories') or []
    covered = {_category_id(row['id']) for row in coverage}
    if (not ids or len(ids) != len(tree) or len(covered) != len(coverage) or ids != covered
            or scans.get('required_categories') != len(ids)
            or scans.get('captured_categories') != len(ids) or scans.get('missing_categories') != []):
        raise ValueError('aru_categories: inconsistent category manifest')
    pages = 0
    for scan in coverage:
        if (type(scan['pages']) is not int or not 1 <= scan['pages'] <= 1000
                or sorted(scan['captured_pages']) != list(range(1, scan['pages'] + 1))):
            raise ValueError('aru_categories: incomplete persisted page coverage')
        pages += scan['pages']
    if pages != data.get('pages') or pages != data.get('captured_pages'):
        raise ValueError('aru_categories: inconsistent page totals')
    observations = sum(row.get('observations', -1) for row in coverage)
    unique = data.get('scanned_products')
    if (not isinstance(unique, int) or unique < 0
            or observations != scans.get('observations') or observations < unique
            or scans.get('unique_products') != unique
            or scans.get('duplicates_reconciled') != observations - unique
            or unique != len(data.get('items') or []) + data.get('excluded_products', -1)):
        raise ValueError('aru_categories: inconsistent product totals')
    names = {row['id']: row.get('name') for row in tree}
    for row in tree:
        path_ids = category_ids(row['id'])
        if (row.get('path_ids') != path_ids or not set(path_ids) <= ids
                or row.get('parent_id') != (path_ids[-2] if len(path_ids) > 1 else None)
                or row.get('url') != 'https://aru.ooo' + row['id']
                or not row.get('name') or row.get('path_names') != [names[p] for p in path_ids]):
            raise ValueError('aru_categories: invalid category hierarchy')
    for row in data.get('items') or []:
        url = urlparse(row.get('url', ''))
        path = '/' + '/'.join(url.path.strip('/').split('/')[:-1]) + '/'
        path_ids = category_ids(path)
        if (url.scheme != 'https' or url.netloc != 'aru.ooo'
                or not row.get('category_ids') or not set(row['category_ids']) <= ids
                or row['category_ids'] != path_ids
                or row.get('category_path') != [names[p] for p in path_ids]
                or row.get('category_slugs') != path.strip('/').split('/')
                or 'pod-zakaz' in row.get('category_slugs', [])
                or row.get('stock_text') != 'В наличии'):
            raise ValueError('aru_categories: invalid product category or stock')
