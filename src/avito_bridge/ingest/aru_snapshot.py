"""Каталог АРУ для предпросмотра и последующего выбора товаров в Авито."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path

from avito_bridge.models import Offer


def fetch_aru_snapshot(cfg):
    options = cfg.source_options or {}
    path = Path(options.get('path') or 'runtime/aru/catalog.json')
    if not path.is_absolute():
        path = Path(cfg.bridge_root) / path
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError('aru_snapshot: snapshot exceeds 50 MiB')
    payload = json.loads(path.read_text(encoding='utf-8'))
    if (payload.get('schema_version') != 1 or payload.get('source') != 'aru'
            or payload.get('complete') is not True):
        raise ValueError('aru_snapshot: complete ARU snapshot required')
    stamp = datetime.fromisoformat(payload['generated_at'].replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('aru_snapshot: timestamp must include timezone')
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    if age < -300:
        raise ValueError('aru_snapshot: future snapshot')
    rows = payload.get('items')
    if not isinstance(rows, list) or not rows or len(rows) > 30000:
        raise ValueError('aru_snapshot: invalid items')
    selected = {str(value) for value in options.get('selected_product_ids', [])}
    product_tags = options.get('product_tags') or {}
    seen, offers = set(), []
    for row in rows:
        identity = str(row.get('id', ''))
        if not identity.isdigit() or identity in seen or not row.get('name'):
            raise ValueError('aru_snapshot: invalid or duplicate product')
        seen.add(identity)
        price = Decimal(str(row['price'])) if row.get('price') is not None else None
        if price is not None and (not price.is_finite() or price <= 0):
            raise ValueError('aru_snapshot: invalid price')
        # Only authenticated, priced stock belongs in the working catalogue.
        if (row.get('available') is not True or price is None
                or row.get('price_basis') != 'account_price'):
            continue
        markup = Decimal(str(options.get('content_markup_pct', 10)))
        if not markup.is_finite() or markup < 0:
            raise ValueError('aru_snapshot: invalid content markup')
        sale_price = (price * (1 + markup / 100)).quantize(Decimal('1'), rounding=ROUND_CEILING)
        tags = product_tags.get(identity) or {}
        approved = (options.get('prices_approved') is True and identity in selected
                    and bool(tags) and row.get('available') is True)
        attrs = {str(k): str(v) for k, v in (row.get('specifications') or {}).items()}
        attrs.update({
            'meta:supplier_article': str(row.get('article', '')),
            'meta:source_url': str(row.get('url', '')),
            'meta:category_name': ' / '.join(row.get('category_path') or []),
            'meta:availability': ('in_stock' if row.get('available') is True else
                                  'out_of_stock' if row.get('available') is False else 'unknown'),
            'meta:stock_is_boolean': '1',
            'meta:account_price': str(price),
            'meta:content_markup_pct': str(markup),
            'meta:content_price': str(sale_price),
            'meta:snapshot_updated_at': payload['generated_at'],
            'meta:price_status': 'approved' if approved else 'awaiting_confirmation',
            'desc_long': str(row.get('description') or row['name']),
        })
        attrs.update({f'avito_tag:{k}': str(v) for k, v in tags.items()})
        offers.append(Offer(supplier_sku=f'aru:{identity}', source='aru',
                            brand=str(row.get('brand', '')), model=row['name'],
                            series=row['name'], stock=int(row.get('available') is True),
                            retail_ref=price, price_override=sale_price if approved else None,
                            attrs=attrs, photos=row.get('image_urls') or []))
    return offers
