import json
from pathlib import Path
from lxml import etree
from avito_bridge.avito.renewal import restore_verified_ads


def test_renewal_keeps_ids_releases_only_verified_archive_and_clears_date_end(tmp_path: Path):
    bridge, public = tmp_path / "bridge", tmp_path / "public"
    bridge.mkdir(); public.mkdir()
    feed, stops = public / "avito-feed.xml", bridge / "stops.json"
    def ad(aid):
        return etree.fromstring(f'<Ad><Id>{aid}</Id><Title>Product</Title><Description>Text</Description><Price>100</Price><Images><Image url="https://example.test/p.jpg"/></Images><DateEnd>2026-01-01</DateEnd></Ad>')
    root = etree.Element('Ads'); root.append(ad('existing'))
    feed.write_bytes(etree.tostring(root))
    stops.write_text(json.dumps({'entries': {'expired': {'reason': 'expired_avito_listing'},
                                           'owner': {'reason': 'manual_avito_removal'},
                                           'unavailable': {'reason': 'archive_hold'}}}))
    before = feed.read_bytes()
    verified = {'expired': ad('expired'), 'owner': ad('owner')}
    preview = restore_verified_ads(feed, stops, verified, bridge, public)
    assert preview['renewed'] == ['expired']
    assert feed.read_bytes() == before
    result = restore_verified_ads(feed, stops, verified, bridge, public, apply=True)
    assert result['status'] == 'committed'
    ads = {a.findtext('Id'): a for a in etree.parse(str(feed)).getroot()}
    assert set(ads) == {'existing', 'expired'}
    assert ads['expired'].find('DateEnd') is None
    assert set(json.loads(stops.read_text())['entries']) == {'owner', 'unavailable'}
