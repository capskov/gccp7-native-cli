"""Read the official assistant's unpacked Beijing construction price cache."""
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
import os
import re
from pathlib import Path
import sqlite3
import shutil
import unicodedata
import uuid
from contextlib import closing


def monthly_packages(root, period, *, copy_to=None):
    if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', period):
        raise ValueError('Use YYYY-MM for the native cache period')
    year, month = period.split('-')
    stem = f'北京_XXJ_{year}_{int(month)}月'
    paths = tuple(root / (stem + suffix) for suffix in ('', '京标价协'))
    if not all(path.is_dir() for path in paths):
        raise ValueError('Download both native information-price packages for the selected month')
    if copy_to is None:
        return paths
    copy_to = copy_to.resolve()
    staging = copy_to.with_name(f'.{copy_to.name}.{uuid.uuid4().hex}.tmp')
    if copy_to.exists():
        raise ValueError('Choose a new folder for the original package snapshot')
    try:
        for path in paths:
            shutil.copytree(path, staging / path.name)
        read_beijing_construction_prices(staging / paths[0].name)
        read_association_prices(staging / paths[1].name)
        os.rename(staging, copy_to)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return tuple(copy_to / path.name for path in paths)


def read_beijing_construction_prices(directory: Path):
    return read_local_price_package(directory, association=False)


def read_local_price_package(directory, *, association):
    suffix = '京标价协' if association else ''
    period = re.fullmatch(r'北京_XXJ_(\d{4})_(\d{1,2})月' + suffix, directory.name)
    if not period or not 1 <= int(period[2]) <= 12:
        raise ValueError('Select an unpacked Beijing construction information-price period')
    chapter = (directory / 'Chapter.dat').read_bytes()
    issuer = '北京市建设工程招标投标和造价管理协会' if association else '北京工程造价信息（建设工程）'
    source = '北京信息价（京标价协）' if association else '北京信息价（房建）'
    if issuer.encode() not in chapter:
        raise ValueError('The package does not identify the reviewed price publisher')
    schema = json.loads((directory / 'TableStructDefine.txt').read_text(encoding='utf-8-sig'))
    materials = schema['Material']
    brands = schema['Brand']
    if set(materials.values()) != {'string', 'brandlist'} or set(brands.values()) != {'string'}:
        raise ValueError('Unsupported information-price record layout')
    payload = (directory / 'Material.dat').read_bytes()
    stream = BytesIO(payload)

    def number():
        raw = stream.read(4)
        if len(raw) != 4:
            raise ValueError('Truncated information-price record')
        return int.from_bytes(raw, 'big')

    def string():
        length = number()
        if length == 0xffffffff:
            return ''
        if length > len(payload) - stream.tell():
            raise ValueError('Invalid information-price string length')
        return stream.read(length).decode('utf-8')

    def record(fields):
        row = {}
        for field, kind in fields.items():
            if kind == 'string':
                row[field] = string()
            else:
                count = number()
                if count > 100:
                    raise ValueError('Unsupported information-price brand count')
                row[field] = [record(brands) for _ in range(count)]
        return row

    if number() != 15:
        raise ValueError('Unsupported information-price file version')
    result = []
    while stream.tell() < len(payload):
        row = record(materials)
        for quote in row['Brand']:
            try:
                tax_included = Decimal(quote['Price'])
                tax_excluded = Decimal(quote['PricePreTaxRate'])
            except InvalidOperation:
                continue
            if not all(price.is_finite() and price > 0 for price in (tax_included, tax_excluded)):
                continue
            result.append(dict(code=row['ID'], material_code=quote['ID'] or row['ID'],
                               material=row['Name'], specification=row['Spec'],
                               unit=row['MainUnit'], brand=quote['BrandName'],
                               ex_tax=format(tax_excluded, 'f'), incl_tax=format(tax_included, 'f'),
                               quote_month=f'{period[1]}-{int(period[2]):02}',
                               source=source, reference=row['NormDefaultIds'], quote_date=quote['QuoteTime']))
    return result


def normalize(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('×', '*')
    return re.sub(r'\s+', '', value)


def match_key(name, specification, unit):
    return tuple(normalize(value) for value in (name, specification, unit))


def read_association_prices(path):
    """Read reviewed transcriptions from the association's own published price table."""
    if path.is_dir():
        return read_local_price_package(path, association=True)
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    report = report.get('data', report)
    period = report['period']
    url = report['url']
    if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', period) or not re.fullmatch(
            r'https://(?:www\.)?bcebca\.org\.cn/(?:HelpUpFile/[^\s]+|Show(?:MaterialPrice|Policy)\.aspx\?id=\d+)', url):
        raise ValueError('Association quotes require the official issue URL and month')
    return [dict(row, source='北京信息价（京标价协）', quote_month=period, reference=url)
            for row in report['quotes']]


def read_market_prices(path):
    with closing(sqlite3.connect(path.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(code=f"{row['capture_id']}:{row['rank']}", material=row['material'],
                     specification=row['specification'], unit=row['unit'], brand=row['brand'],
                     model=row['model'], supplier=row['supplier'], dn=row['dn'],
                     ex_tax=str(Decimal(str(row['ex_tax_cents'])) / 100),
                     incl_tax=str(Decimal(str(row['incl_tax_cents'])) / 100),
                     quote_month=row['quote_month'][:7], quote_date=row['quote_month'], source='市场价',
                     reference=row['source'], remark=row['remark'], tax_rate=row['tax_rate'],
                     document_label=row['quote_document_label'])
                for row in db.execute('SELECT q.*, c.source FROM quote q JOIN capture c ON q.capture_id = c.id')]


def combine_assistant_library(market_path, output, information_offers):
    """Create a compatible copy of the assistant library and append local information prices."""
    source_path = market_path.resolve(strict=True)
    target = output.resolve()
    if target.exists():
        raise ValueError('Choose a new assistant database path')
    staging = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    groups = {}
    for offer in information_offers:
        groups.setdefault((offer['source'], offer['quote_month']), []).append(offer)
    with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)) as source:
        try:
            with closing(sqlite3.connect(staging)) as db:
                source.backup(db)
                for (name, month), offers in groups.items():
                    capture = db.execute('INSERT INTO capture (capture_date, source, search_text, region_filter, '
                        'time_filter, loaded_rows, capture_limit, method, candidate_rule) VALUES(?,?,?,?,?,?,?,?,?)',
                        (month, name, '', '北京', month, len(offers), len(offers), 'local_information_price', '')).lastrowid
                    db.execute('INSERT INTO quote_category VALUES(?, ?)', (capture, name))
                    db.executemany('INSERT INTO quote VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                        (capture, i, item['material'], 0, '', item['specification'], '北京', item['unit'],
                         item.get('brand', ''), '', str(Decimal(item['ex_tax']) * 100),
                         str(Decimal(item['incl_tax']) * 100), '', month, item['reference'], item['code'], 0,
                         json.dumps(item, ensure_ascii=False)) for i, item in enumerate(offers, start=1)))
                db.commit()
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Assistant library integrity check failed')
                total = db.execute('SELECT COUNT(*) FROM quote').fetchone()[0]
            os.link(staging, target)
        finally:
            staging.unlink(missing_ok=True)
    return dict(path=str(target), information_count=len(information_offers), total_count=total)


def plan_prices(rows, construction, association, *, tax_basis, aliases=(), market=()):
    """Choose the highest original quote among reviewed comparable products from the preferred source."""
    by_target = {}
    for alias in aliases:
        target = tuple(alias['target'])
        choices = alias.get('quotes', [alias['quote']] if 'quote' in alias else [])
        if (len(target) != 5 or target in by_target or not alias['reason'].strip() or not choices
                or any(len(choice) != 3 or normalize(choice[2]) != normalize(target[4]) for choice in choices)):
            raise ValueError('Provide documented same-unit alternatives for one exact target')
        by_target[target] = alias, [match_key(*choice) for choice in choices]
    catalogs = []
    for offers in (construction, association, market):
        indexed = {}
        for quote in offers:
            key = match_key(quote['material'], quote['specification'], quote['unit'])
            indexed.setdefault(key, []).append(quote)
        catalogs.append(indexed)
    items, unresolved = [], []
    unchanged = 0
    for row in rows:
        target = tuple(row[:5])
        if not target[0] or Decimal(row[5] or '0') <= 0 or Decimal(row[6] or '0') != 0 or row[9] != 'false':
            unchanged += 1
            continue
        exact = match_key(*target[2:5])
        alias, alternatives = by_target.get(target, ({}, []))
        matched = None
        for indexed in catalogs:
            for keys in ([exact], alternatives):
                valid = []
                for key in dict.fromkeys(keys):
                    for quote in indexed.get(key, []):
                        try:
                            value = Decimal(str(quote[tax_basis]))
                        except (InvalidOperation, KeyError):
                            continue
                        if value.is_finite() and value > 0 and value.normalize().as_tuple().exponent >= -6:
                            valid.append((key, value, quote))
                if valid:
                    brand = alias.get('preferred_brand')
                    if brand:
                        preferred = [offer for offer in valid if brand in offer[2].get('brand', '')]
                        if preferred:
                            valid = preferred
                    matched = max(valid, key=lambda offer: (offer[1], offer[2]['quote_month']))
                    break
            if matched:
                break
        if not matched:
            unresolved.append(dict(target=target, reason='no_matching_quote'))
            continue
        key, value, quote = matched
        borrowed = alias if key != exact else None
        price = format(value.normalize(), 'f')
        source = native_price_source(quote)
        items.append(dict(target=target, price=price, source=source, quote=quote, tax_basis=tax_basis,
                          reason=borrowed['reason'] if borrowed else 'exact'))
    return dict(items=items, unresolved=unresolved, unchanged=unchanged, tax_basis=tax_basis)


def native_price_source(quote):
    period = quote['quote_month']
    if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', period):
        raise ValueError('A verified quotation month is required')
    year, month = period.split('-')
    if quote['source'] == '北京信息价（京标价协）':
        return f'北京信息价({year}年{month}月京标价协)'
    if quote['source'] == '北京信息价（房建）':
        return f'北京信息价({year}年{int(month)}月)'
    if quote['source'] == '市场价':
        date = re.fullmatch(r'\d{4}-\d{2}-(0[1-9]|[12][0-9]|3[01])', quote.get('quote_date', ''))
        day = f'{int(date[1])}日' if date and quote['quote_date'].startswith(period) else ''
        return f'市场价({year}年{int(month)}月{day})'
    return f"{quote['source']}({year}年{int(month)}月)"
