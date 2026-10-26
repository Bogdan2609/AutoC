"""Strict, streaming adapters for the TWO verified supplier file formats.

No customer order or API provider interaction. All quantities derived from ranges
are conservative lower bounds; never convert them into an exact stock count.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterator
from zipfile import ZipFile, BadZipFile

from app.modules.catalog.normalize import normalize_article, normalize_brand

MAX_FILE = 48 * 1024 * 1024
MAX_XLSX_UNPACKED = 350 * 1024 * 1024
MAX_XML = 260 * 1024 * 1024
MAX_CELL_TEXT = 3000


class FeedFormatError(ValueError):
    pass


@dataclass(frozen=True)
class FeedRow:
    line: int
    sku: str
    brand: str
    article: str
    description: str
    warehouse: str
    purchase_price: Decimal
    stock_raw: str
    stock_min: int
    stock_is_lower_bound: bool
    minimum: int
    multiple: int
    lead_days: int | None
    brand_norm: str
    article_norm: str
    identity: str


def _safe_archive(z: ZipFile, *, max_size: int, max_members: int = 100) -> None:
    infos = z.infolist()
    if len(infos) > max_members or not infos:
        raise FeedFormatError('Invalid archive member count')
    if sum(i.file_size for i in infos) > max_size:
        raise FeedFormatError('Uncompressed archive too large')
    for m in infos:
        name = m.filename.replace('\\', '/')
        if name.startswith('/') or '..' in name.split('/') or m.flag_bits & 1 or m.is_dir() and name.rstrip('/') == '':
            raise FeedFormatError('Unsafe archive member')
        if m.file_size > MAX_XML and (name.endswith('.xml') or name.endswith('.bin')):
            raise FeedFormatError('Oversized archive member')
        if name.lower().endswith(('.xlsm', '.xlsb', '.exe', '.js', '.vbs')):
            raise FeedFormatError('Executable / macro member disallowed')


def _positive_int(v: object, *, default: int | None = None) -> int:
    s = str(v or '').strip()
    if not s and default is not None:
        return default
    if not re.fullmatch(r'[0-9]{1,9}', s):
        raise ValueError('Invalid positive integer')
    n = int(s)
    if n < 1:
        raise ValueError('Expected positive integer')
    return n


def parse_stock(v: object) -> tuple[str, int, bool]:
    raw = str(v or '').strip().replace(' ', '')
    if re.fullmatch(r'\d{1,9}', raw):
        return raw, int(raw), False
    m = re.fullmatch(r'>\s*(\d{1,9})', raw)
    if m:
        return raw, int(m.group(1)) + 1, True
    m = re.fullmatch(r'(\d{1,9})[-–](\d{1,9})', raw)
    if m and int(m.group(1)) <= int(m.group(2)):
        return raw, int(m.group(1)), True
    raise ValueError('Invalid or unknown supplier stock representation')


def _decimal(v: object) -> Decimal:
    try:
        n = Decimal(str(v or '').strip().replace(',', '.'))
        if not n.is_finite() or not 0 < n <= Decimal('9999999999'):
            raise ValueError('Price out of range')
        n = n.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if n <= 0:
            raise ValueError('Zero rounded price')
        return n
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Invalid purchase price') from exc


def _row(*, line: int, supplier: str, sku: str, brand: str, article: str,
         description: str, warehouse: str, price: object, stock: object,
         minimum: object, multiple: object, days: object) -> FeedRow:
    sku, brand, article, warehouse = [str(x or '').strip() for x in (sku, brand, article, warehouse)]
    description = str(description or '').strip()
    if not brand or not article or not warehouse or len(brand) > 160 or len(article) > 110 or len(warehouse) > 140 or len(sku) > 150 or len(description) > 3000:
        raise ValueError('Missing/oversized article, brand, SKU, warehouse or description')
    bn, an = normalize_brand(brand), normalize_article(article)
    if not bn or not an or len(an) > 110 or len(bn) > 160:
        raise ValueError('Invalid normalized article or brand')
    if supplier == 'rossko' and not sku:
        raise ValueError('Missing Rossko product identity')
    raw, qty, lower = parse_stock(stock)
    min_units, mult = _positive_int(minimum, default=1), _positive_int(multiple, default=1)
    delay = None if days is None or str(days).strip() == '' else int(str(days).strip())
    if delay is not None and (delay < 0 or delay > 365):
        raise ValueError('Impossible lead time')
    # SKU is the stable Rossko nomenclature ID, not the article number.
    # The PART-KOM file has no offer ID: brand+article+warehouse is its key.
    key = f'{sku}\x1f{warehouse}' if supplier == 'rossko' else f'{bn}\x1f{an}\x1f{warehouse}'
    import hashlib
    return FeedRow(line, sku, brand, article, description, warehouse,
                   _decimal(price), raw, qty, lower, min_units, mult, delay, bn, an,
                   hashlib.sha256(key.encode('utf-8')).hexdigest())


def _partkom(path: Path) -> Iterator[tuple[int, FeedRow | None, str | None]]:
    with path.open('r', encoding='cp1251', newline='') as stream:
        reader = csv.DictReader(stream, delimiter=';', strict=True)
        expected = ['Артикул', 'Бренд', 'Описание', 'Цена', 'Количество', 'Мин.Партия', 'Склад']
        if reader.fieldnames != expected:
            raise FeedFormatError(f'PART-KOM CSV header mismatch: {reader.fieldnames!r}')
        for line, r in enumerate(reader, start=2):
            try:
                if None in r:
                    raise ValueError('Unexpected extra columns')
                row = _row(line=line, supplier='partkom', sku='', brand=r['Бренд'],
                           article=r['Артикул'], description=r['Описание'], warehouse=r['Склад'],
                           price=r['Цена'], stock=r['Количество'], minimum=r['Мин.Партия'],
                           multiple=r['Мин.Партия'], days=None)
                yield line, row, None
            except (ValueError, TypeError, KeyError) as exc:
                yield line, None, str(exc)[:180]


@contextmanager
def _xlsx_stream(path: Path):
    """Open a Rossko ZIP containing precisely one macro-free XLSX or an XLSX directly.

    Nested archive copied to SpooledTemporaryFile; no path is extracted.
    """
    is_outer_zip = path.suffix.lower() == '.zip'
    if not is_outer_zip:
        with ZipFile(path) as book:
            _safe_archive(book, max_size=MAX_XLSX_UNPACKED)
            yield book
        return
    with ZipFile(path) as outer:
        _safe_archive(outer, max_size=MAX_FILE, max_members=3)
        entries = [i for i in outer.infolist() if not i.is_dir()]
        if len(entries) != 1 or not entries[0].filename.lower().endswith('.xlsx'):
            raise FeedFormatError('ROSSKO ZIP must contain exactly one XLSX')
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as temp:
            with outer.open(entries[0]) as source:
                shutil.copyfileobj(source, temp, length=1024 * 1024)
            temp.seek(0)
            with ZipFile(temp) as book:
                _safe_archive(book, max_size=MAX_XLSX_UNPACKED)
                yield book


def _excel_col(ref: str) -> int:
    m = re.match('[A-Z]+', ref or '')
    if not m:
        raise FeedFormatError('Invalid cell address')
    idx = 0
    for c in m.group():
        idx = idx * 26 + ord(c) - 64
    return idx - 1


def _rossko(path: Path) -> Iterator[tuple[int, FeedRow | None, str | None]]:
    # defusedxml prevents internal entity expansion; spreadsheet formulas are
    # ignored and never evaluated. Read-only XML iteration bounds peak memory.
    from defusedxml.ElementTree import iterparse
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with _xlsx_stream(path) as book:
        names = set(book.namelist())
        if 'xl/worksheets/sheet1.xml' not in names or 'xl/sharedStrings.xml' not in names or any(n.startswith('xl/externalLinks/') or 'vbaProject' in n for n in names):
            raise FeedFormatError('Unexpected ROSSKO workbook structure')
        strings: list[str] = []
        with book.open('xl/sharedStrings.xml') as data:
            for _, el in iterparse(data, events=('end',)):
                if el.tag == ns + 'si':
                    s = ''.join(t.text or '' for t in el.iter(ns + 't'))
                    if len(s) > MAX_CELL_TEXT:
                        raise FeedFormatError('Oversized string in workbook')
                    strings.append(s)
                    el.clear()
        header = ['Номенклатура', 'Бренд', 'Артикул', 'Описание', 'Вес/Объем',
                  'Кратность отгрузки', 'Цена, руб.', 'Базовая цена, руб.', 'Наличие',
                  'Срок поставки, дн.', 'Каталожный номер', 'OEМ Номер', 'Применимость',
                  'Вендор-код', 'ТНВЭД', 'ОКПД 2', 'Подключен к ЧЗ', 'Склад']
        with book.open('xl/worksheets/sheet1.xml') as data:
            for _, el in iterparse(data, events=('end',)):
                if el.tag != ns + 'row':
                    continue
                line = int(el.attrib.get('r') or '0')
                cells: dict[int, str] = {}
                for c in el.iter(ns + 'c'):
                    if c.find(ns + 'f') is not None:
                        raise FeedFormatError('Formula cell in supplier price file')
                    v = c.find(ns + 'v')
                    iv = c.find(ns + 'is')
                    raw = v.text if v is not None and v.text is not None else ''
                    if c.attrib.get('t') == 's':
                        try:
                            raw = strings[int(raw)]
                        except (ValueError, IndexError):
                            raise FeedFormatError('Invalid shared string index') from None
                    elif c.attrib.get('t') == 'inlineStr':
                        raw = ''.join(t.text or '' for t in iv.iter(ns + 't')) if iv is not None else ''
                    if len(raw) > MAX_CELL_TEXT:
                        raise FeedFormatError('Oversized cell in workbook')
                    cells[_excel_col(c.attrib.get('r', ''))] = raw
                el.clear()
                if line == 1:
                    if [cells.get(i, '') for i in range(18)] != header:
                        raise FeedFormatError('ROSSKO XLSX columns changed')
                    continue
                if line < 2:
                    continue
                try:
                    row = _row(line=line, supplier='rossko', sku=cells.get(0, ''),
                               brand=cells.get(1, ''), article=cells.get(2, ''),
                               description=cells.get(3, ''), warehouse=cells.get(17, ''),
                               price=cells.get(6, ''), stock=cells.get(8, ''),
                               minimum=cells.get(5, ''), multiple=cells.get(5, ''),
                               days=cells.get(9, ''))
                    yield line, row, None
                except (ValueError, TypeError) as exc:
                    yield line, None, str(exc)[:180]


def read_feed(supplier: str, path: Path) -> Iterator[tuple[int, FeedRow | None, str | None]]:
    if path.stat().st_size > MAX_FILE:
        raise FeedFormatError('File exceeds maximum size')
    if supplier == 'partkom' and path.suffix.lower() == '.csv':
        yield from _partkom(path)
    elif supplier == 'rossko' and path.suffix.lower() in ('.zip', '.xlsx'):
        yield from _rossko(path)
    else:
        raise FeedFormatError('Unsupported file type for supplier')
