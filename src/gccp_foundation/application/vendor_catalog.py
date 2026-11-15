"""Search the bundled Beijing 2025 GCCP export or read the installed source."""
import base64
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

from .native_cli import Rejected


LIBRARY = '北京市建设工程概算消耗量标准(2025)'
SCHEMA = 'gccp.vendor-subitem-index.v1'
TRADE_BLOCK = 100_000_000


def default_library_root():
    return (Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Glodon' / '数据库30.0'
            / '北京' / '定额库' / '北京25概算标准' / '定额库' / LIBRARY)


def read_entries(data, trades):
    """Decode repeated little-endian integers and byte-length-prefixed strings."""
    position = 0

    def read_word():
        nonlocal position
        value, = struct.unpack_from('<I', data, position)
        position += 4
        return value

    def read_string(encoding):
        nonlocal position
        length = data[position]
        position += 1
        end = position + length
        if end > len(data):
            raise ValueError('truncated string')
        value = data[position:end].decode(encoding)
        position = end
        return value

    entries = []
    seen = set()
    while position < len(data):
        start = position
        try:
            item_id = read_word()
            code = read_string('ascii')
            name = read_string('gb18030')
            group_id = read_word()
            specialty = trades[group_id // TRADE_BLOCK * TRADE_BLOCK]
            signature = base64.b64decode(read_string('ascii'), validate=True)
            if not item_id or item_id in seen or not code or not name or len(signature) != 8:
                raise ValueError('invalid subitem record')
        except (struct.error, IndexError, KeyError, ValueError) as exc:
            raise Rejected('VENDOR_INDEX_FORMAT', f'Unrecognized index record at byte {start}') from exc
        seen.add(item_id)
        entries.append(dict(vendor_id=item_id, code=code, name=name, specialty=specialty, group_id=group_id))
    if not entries:
        raise Rejected('VENDOR_INDEX_FORMAT', 'The installed index is empty')
    return entries


def installed_index(library_root):
    root = library_root.resolve(strict=True)
    if root.name != LIBRARY:
        raise Rejected('VENDOR_LIBRARY_UNSUPPORTED', f'Expected the installed {LIBRARY} directory')
    index = root / '数据' / '子目索引.Index'
    trade_file = root / '基础数据' / '子目专业.xml'
    index_bytes = index.read_bytes()
    trade_bytes = trade_file.read_bytes()
    try:
        trades = {int(row.attrib['TradeID']): row.attrib['Abbr'] for row in ET.fromstring(trade_bytes).iter('Record')}
    except (ET.ParseError, KeyError, ValueError) as exc:
        raise Rejected('VENDOR_INDEX_FORMAT', 'Unrecognized installed trade mapping') from exc
    entries = read_entries(index_bytes, trades)
    metadata = dict(
        schema=SCHEMA, library=LIBRARY, library_root=str(root), source='installed',
        index_sha256=hashlib.sha256(index_bytes).hexdigest(),
        trades_sha256=hashlib.sha256(trade_bytes).hexdigest(),
        indexed_count=len(entries),
        unit_available=False, live_candidate_check_required=True,
    )
    return metadata, entries


def bundled_index():
    resource = files('gccp_foundation').joinpath('resources', 'catalog', 'beijing-2025-gccp-index.json')
    try:
        payload = json.loads(resource.read_text(encoding='utf-8'))
        entries = payload.pop('entries')
        if (payload['schema'] != SCHEMA or payload['library'] != LIBRARY
                or not entries or payload['indexed_count'] != len(entries)
                or payload.pop('matched_count') != len(entries)):
            raise ValueError('Expected a complete Beijing 2025 export')
    except FileNotFoundError:
        root = default_library_root()
        if not root.is_dir():
            raise Rejected('VENDOR_LIBRARY_NOT_FOUND', 'Install the Beijing 2025 library or supply --library-root')
        return installed_index(root)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise Rejected('BUNDLED_VENDOR_INDEX_INVALID', 'Reinstall the CLI with its complete Beijing 2025 export') from exc
    return {**payload, 'source': 'bundled'}, entries


def vendor_index(library_root=None, *, code=None, text=None, specialty=None, vendor_id=None, output=None, limit=20):
    if not 1 <= limit <= 100:
        raise Rejected('LIMIT_INVALID', 'limit must be between 1 and 100')
    if not (code or text or specialty or vendor_id is not None or output):
        raise Rejected('VENDOR_QUERY_REQUIRED', 'Provide a filter or --output for a full export')
    metadata, entries = bundled_index() if library_root is None else installed_index(library_root)
    terms = (text or '').split()
    matches = [row for row in entries
               if (vendor_id is None or row['vendor_id'] == vendor_id)
               and (code is None or row['code'] == code)
               and (specialty is None or row['specialty'] == specialty)
               and all(term in row['name'] for term in terms)]
    metadata['matched_count'] = len(matches)
    if output is None:
        return {**metadata, 'entries': matches[:limit], 'truncated': len(matches) > limit}
    target = output.resolve()
    # Exclusive creation prevents replacing either a vendor file or an earlier export.
    with target.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump({**metadata, 'entries': matches}, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return {**metadata, 'output': str(target), 'exported_count': len(matches)}


def vendor_entry(library_root, vendor_id):
    result = vendor_index(library_root, vendor_id=vendor_id)
    if not result['entries']:
        raise Rejected('VENDOR_ENTRY_NOT_FOUND', f'No Beijing 2025 subitem with vendor ID {vendor_id}')
    return {**result['entries'][0], 'library': result['library'],
            'source': result['source'],
            'index_sha256': result['index_sha256'], 'trades_sha256': result['trades_sha256']}


def match_live_entry(entry, lookup_result):
    if lookup_result['library'] != entry['library']:
        raise Rejected('VENDOR_LIBRARY_MISMATCH', 'The active GCCP library differs from the vendor index')
    fields = ('code', 'name', 'specialty')
    matches = [candidate for candidate in lookup_result['candidates']
               if all(candidate[field] == entry[field] for field in fields)]
    if len(matches) != 1 or not matches[0]['unit']:
        raise Rejected('VENDOR_CANDIDATE_NOT_UNIQUE',
                       'Expected one live candidate with matching code, full name, specialty and a usable unit')
    return matches[0]
