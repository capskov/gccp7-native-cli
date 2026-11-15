"""Apply reviewed PDF header corrections without changing live quota policy."""
from contextlib import closing
import json
import sqlite3

from .native_cli import Rejected


EDITABLE_FIELDS = {'name', 'spec', 'spec_descriptor', 'unit'}


def apply_review(path, review_path):
    review = json.loads(review_path.read_text(encoding='utf-8-sig'))
    source = review['source']
    changes = review['entries']
    codes = [change['code'] for change in changes]
    if not codes or len(set(codes)) != len(codes):
        raise Rejected('CATALOG_REVIEW_INVALID', 'Each reviewed source code must appear exactly once')
    with closing(sqlite3.connect(path.resolve(strict=True).as_uri() + '?mode=rw', uri=True)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        matched_sources = db.execute('SELECT source_id FROM sources WHERE filename=? AND sha256=?',
                                     (source['filename'], source['sha256'])).fetchall()
        if len(matched_sources) != 1:
            raise Rejected('CATALOG_SOURCE_MISMATCH', 'The reviewed PDF fingerprint must match one catalog source')
        changed_entries = changed_fields = 0
        for change in changes:
            matched = db.execute('SELECT * FROM quota_entries WHERE source_id=? AND code=?',
                                 (matched_sources[0]['source_id'], change['code'])).fetchall()
            if len(matched) != 1 or matched[0]['pdf_page'] != change['pdf_page']:
                raise Rejected('CATALOG_ENTRY_MISMATCH', 'Reviewed code and PDF page must match: ' + change['code'])
            row = matched[0]
            before, after = change['before'], change['after']
            if not before or before.keys() != after.keys() or before.keys() - EDITABLE_FIELDS:
                raise Rejected('CATALOG_REVIEW_INVALID', 'Only reviewed name, specification, and unit fields are supported')
            if 'unit' in after and (not isinstance(after['unit'], str) or not after['unit'].strip()):
                raise Rejected('CATALOG_REVIEW_INVALID', 'A reviewed unit must be a nonempty string')
            current = {field: row[field] for field in before}
            if current == after:
                continue
            if current != before:
                raise Rejected('CATALOG_ENTRY_CHANGED', 'Existing fields differ from the reviewed originals: ' + change['code'])
            reference = f"{source['url']}#page={change['pdf_page']}"
            for field, value in after.items():
                if before[field] == value:
                    continue
                db.execute(f'UPDATE quota_entries SET {field}=? WHERE id=?', (value, row['id']))
                db.execute('''INSERT INTO quota_revision_history
                    (entry_id,field_name,old_value,new_value,reason,source) VALUES(?,?,?,?,?,?)''',
                    (row['id'], field, before[field], value, review['reason'], reference))
                changed_fields += 1
            if 'name' in after:
                db.execute('UPDATE quota_audit SET corrected_name=? WHERE entry_id=?', (after['name'], row['id']))
            db.execute('''INSERT INTO quota_review_progress
                (entry_id,status,checked_fields,checked_pdf_page,reference_url,evidence_note,reviewed_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(entry_id) DO UPDATE SET status=excluded.status,checked_fields=excluded.checked_fields,
                checked_pdf_page=excluded.checked_pdf_page,reference_url=excluded.reference_url,
                evidence_note=excluded.evidence_note,reviewed_at=excluded.reviewed_at''',
                (row['id'], 'official_pdf_header_checked', json.dumps(sorted(after)), change['pdf_page'],
                 reference, review['reason'], review['reviewed_at']))
            changed_entries += 1
        return dict(source=source['filename'], reviewed_entries=len(codes), changed_entries=changed_entries,
                    changed_fields=changed_fields, automatic_insertion_approved=False)
