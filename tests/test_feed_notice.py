from lxml import etree

from avito_bridge.feed.notice import DEFAULT_NOTICE, prepend_notice


def test_prepend_notice_changes_only_descriptions_and_is_idempotent(tmp_path):
    source = tmp_path / "source.xml"
    target = tmp_path / "target.xml"
    source.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Ads formatVersion="3" target="Avito.ru">'
        '<Ad><Id>a1</Id><Title>One</Title><Price>100</Price>'
        '<Description>Text</Description></Ad>'
        '<Ad><Id>a2</Id><Title>Two</Title><Price>200</Price>'
        f'<Description>{DEFAULT_NOTICE}\n\nExisting</Description></Ad>'
        '</Ads>',
        encoding="utf-8",
    )

    changed, total = prepend_notice(source, target)
    assert (changed, total) == (1, 2)
    root = etree.parse(str(target)).getroot()
    assert [ad.findtext("Price") for ad in root.findall("Ad")] == ["100", "200"]
    assert all(
        (ad.findtext("Description") or "").startswith(DEFAULT_NOTICE)
        for ad in root.findall("Ad")
    )

    changed_again, _ = prepend_notice(target, target)
    assert changed_again == 0
