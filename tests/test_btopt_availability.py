import pytest
from datetime import date
from types import SimpleNamespace
import avito_bridge.ingest.btopt_availability as module
from avito_bridge.ingest.btopt_availability import availability

@pytest.mark.parametrize('value,expected', [('В наличии',True), (None,None), ('',None),
    ('Под заказ',False), ('0',False), (2,True), ('нет',False)])
def test_stock_labels(value,expected):
    assert availability(value) is expected

@pytest.mark.parametrize('value', ['неизвестно', -1, 'nan', 'inf'])
def test_unknown_is_not_zero(value):
    with pytest.raises(ValueError):
        availability(value)

def test_current_schema_image_id_is_not_price_or_stock(monkeypatch, tmp_path):
    source = tmp_path / 'price.xlsx'
    source.write_bytes(b'fixture')
    header = [None] * 17
    for i,v in {0:'Артикул',4:'Номенклатура',13:'Изображение',14:'от 100 т.руб/мес',15:'Склад основной ООО',16:'Склад управленки'}.items():
        header[i] = v
    product = [None] * 17
    for i,v in {0:'SKU1',4:'Товар',13:'777',14:'1\xa0000,00',15:'В наличии'}.items():
        product[i] = v
    rows = [['Прайс-лист на 20 сентября 2026 г.'], [], header, [], product]
    sheet = SimpleNamespace(max_row=5, iter_rows=lambda **kwargs: iter(rows))
    monkeypatch.setattr(module,'load_workbook',lambda *a,**kw: SimpleNamespace(worksheets=[sheet],close=lambda:None))
    parsed = module.read_availability(source,today=date(2026,9,20))
    assert parsed['items']['SKU1']['main_available'] is True
    assert parsed['items']['SKU1']['management_available'] is None
    assert 'absent' not in parsed['items']
    with pytest.raises(ValueError, match='Stale'):
        module.read_availability(source,today=date(2026,9,24))
    header[13] = 'Changed'
    with pytest.raises(ValueError, match='schema'):
        module.read_availability(source,today=date(2026,9,20))
