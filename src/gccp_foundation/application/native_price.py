"""Read and update zero-priced rows in the native GCCP resource summary."""
from contextlib import closing, contextmanager
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from queue import Empty
import re
import sys
import time
import uuid

from .information_prices import native_price_source, normalize
from .native_build import TEMPLATES, compile_bridge, profile_path
from .native_cli import Rejected, digest, encoded, initialize_commands
from .native_delete import load_bridge, save_result
from .native_runtime import _replace, render_transport
from .native_worker_support import (
    Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, file_sha256, project_window_prefix, table,
)
from .ports import SessionContext
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts

SUMMARY_HEADERS = ['编码', '类别', '名称', '规格型号', '单位', '数量', '市场价', '市场价合计',
                   '价格来源', '市场价锁定', '输出标记', '产地', '厂家', '品牌', '送达地点',
                   '质量等级', '供应时间', '备注']
SUMMARY_HEADER_VARIANTS = (SUMMARY_HEADERS, SUMMARY_HEADERS[:16] + ['供货时间', '备注'])


@contextmanager
def assistant_link(assistant_root, ctx):
    if assistant_root is None:
        from gccp_material_price_assistant.gccp_link import GccpLink
        link = GccpLink()
        try:
            link.connect()
        except ImportError as exc:
            raise ValueError('Install the assistant extra: gccp7-native-cli[assistant]') from exc
    else:
        root = Path(assistant_root).resolve(strict=True)
        sys.path[:0] = [str(root), str(root / '.venv/Lib/site-packages')]
        try:
            from gccp_link import GccpLink
            link = GccpLink()
            link.connect()
        finally:
            del sys.path[:2]
    with closing(link):
        if link.pid != ctx.pid:
            raise ValueError('The original pricing assistant connected to another process')
        yield link


def send_assistant_quote(link, bridge, row, source, item):
    while not link.events.empty():
        event, _ = link.events.get_nowait()
        if event == 'detached':
            raise ValueError('The native pricing assistant disconnected')
    bridge.call('price.select', expected_target=dict(row=row, replace='expected_current' in item))
    try:
        event, selection = link.events.get(timeout=5)
    except Empty as exc:
        raise ValueError('No native resource selection received from the pricing assistant') from exc
    if event != 'selection' or not selection:
        raise ValueError('A single native resource selection is required')
    body = selection['body']
    material = body['Materials'][0]
    if ((str(material['Code']), str(material['Unit'])) != (row[0], row[4])
            or normalize(str(material['Name']) + str(material['Spec'])) != normalize(row[2] + row[3])
            or Decimal(str(material['Quantity'])) != Decimal(row[5])):
        raise ValueError('Assistant selected a different resource')
    tax_basis = item.get('tax_basis', 'ex_tax')
    if body['TaxMethod'] != (1 if tax_basis == 'ex_tax' else 0):
        raise ValueError('Project tax basis differs from the reviewed quote')
    offer = item['quote']
    rates = {basis: Decimal(str(offer[basis])) for basis in ('ex_tax', 'incl_tax')}
    cents = {basis + '_cents': value * 100 for basis, value in rates.items()}
    link.send_quote(selection, dict(cents, material=offer.get('material') or row[2],
                                    specification=offer.get('specification') or row[3],
                                    model=offer.get('model', ''), source=source, native_source=source,
                                    quote_month=offer.get('quote_date') or offer.get('quote_month', ''),
                                    brand=offer.get('brand', ''), supplier=offer.get('supplier', ''),
                                    material_code=offer.get('material_code', ''),
                                    glj_price_code=offer.get('glj_price_code', '')),
                    Decimal('1'), precision=max(2, *(-value.as_tuple().exponent for value in rates.values())))


def summary(capture):
    matches = [tab for tab in capture['tables'] if tab['complete'] and tab['headers'] in SUMMARY_HEADER_VARIANTS]
    if len(matches) != 1:
        raise Rejected('PRICE_SUMMARY_NOT_UNIQUE', 'Open the native resource summary with all resources visible')
    return [row['values'] for row in matches[0]['rows']]


def indexed_summary(capture):
    rows = summary(capture)
    indexed = {tuple(row[:6]): row for row in rows}
    if len(indexed) != len(rows):
        raise Rejected('PRICE_SUMMARY_IDENTITY_DUPLICATE', 'Resource summary contains indistinguishable rows')
    return indexed


def reviewed_price(item):
    target = tuple(item['target'])
    if len(target) != 5 or not all(isinstance(value, str) for value in target) or not target[0]:
        raise Rejected('PRICE_TARGET_INVALID', 'Provide the reviewed code, kind, name, specification and unit')
    price = str(item['price'])
    if not re.fullmatch(r'(?:0|[1-9]\d{0,7})(?:\.\d{1,6})?', price) or Decimal(price) <= 0:
        raise Rejected('PRICE_INVALID', 'Provide a positive decimal price in the project tax basis and target unit')
    source = item['source']
    if not source.strip() or len(source) > 120:
        raise Rejected('PRICE_SOURCE_INVALID', 'Provide the reviewed source and period')
    return target, price, source


def matches_current_price(item, row):
    expected = item.get('expected_current')
    if expected is None:
        return Decimal(row[6]) == 0
    return (set(expected) == {'price', 'source', 'quantity'}
            and bool(expected['source'])
            and Decimal(row[6]) == Decimal(str(expected['price']))
            and row[8] == expected['source'] and row[5] == str(expected['quantity']))


def build_price_bridge(directory, command, meta, profile):
    files = render_transport(TEMPLATES, directory, command, meta['session'], profile,
                             project_window_prefix(meta['workcopy_path']))
    files['operations.h'] = (TEMPLATES / 'price_edit.h.in').read_text(encoding='utf-8')
    guard = 'QStringList{"insert","material.confirm","note.commit","quantity.commit","name.set","quantity.set","quantity.normal.zero","quantity.normal.one","save"}.contains(op)'
    files['bridge.cpp'] = _replace(files['bridge.cpp'], guard, 'op == "price.confirm"')
    directory.mkdir(parents=True)
    for name, content in files.items():
        (directory / name).write_text(content, encoding='utf-8', newline='\n')
    compile_bridge(directory)
    return load_bridge(directory)


def verify_price_delta(before, after, key, price, source, source_set, *, staged=False):
    return verify_price_batch(before, after, {key: (price, source if source_set else before[key][8])},
                              staged=staged)[key]


def verify_price_batch(before, after, changes, *, staged=False, quote_metadata=None):
    def same_price(value, expected):
        return abs(Decimal(value) - Decimal(expected)) <= Decimal('0.000000001')

    def is_derived(row):
        return row[2].startswith('其他') and row[3].startswith('占') and row[4] == '元' and row[6] == '1'

    def derived_rows(snapshot):
        return sorted(tuple(row[:5] + row[6:7] + row[8:]) for row in snapshot.values() if is_derived(row))

    if not staged:
        if derived_rows(before) != derived_rows(after):
            raise ValueError('Percentage-based resource identities changed')
        before = {identity: row for identity, row in before.items() if not is_derived(row)}
        after = {identity: row for identity, row in after.items() if not is_derived(row)}
    observed = {}
    before, after = before.copy(), after.copy()
    if not staged:
        for key, (price, source) in changes.items():
            if key in after:
                continue
            peers = [identity for identity, row in before.items()
                     if identity[:5] == key[:5] and identity not in changes and same_price(row[6], price)]
            if not peers:
                raise ValueError('The priced resource could not be located')
            entries = [before[key]] + [before[identity] for identity in peers]
            quantity = sum(Decimal(row[5]) for row in entries)
            candidates = [identity for identity, row in after.items() if identity[:5] == key[:5]
                          and Decimal(row[5]) == quantity and same_price(row[6], price)]
            if len(candidates) != 1:
                raise ValueError('The combined resource quantity differs')
            merged = after.pop(candidates[0])
            if (merged[9:] != before[key][9:] or any(row[9:] != merged[9:] for row in entries)
                    or merged[8] not in {'', source, *(row[8] for row in entries)}
                    or abs(Decimal(merged[7]) - quantity * Decimal(price)) > Decimal('0.02')):
                raise ValueError('The combined resource total or metadata differs')
            observed[key] = merged
            for identity in [key, *peers]:
                del before[identity]
    if before.keys() != after.keys():
        raise ValueError('The resource summary identities changed')
    for identity, row in before.items():
        actual = after[identity]
        if identity in changes:
            price, source = changes[identity]
            offer = (quote_metadata or {}).get(identity, {})
            if (actual[:6] != row[:6] or actual[9:12] != row[9:12] or actual[14:] != row[14:]
                    or actual[12] not in (row[12], offer.get('supplier'))
                    or actual[13] not in (row[13], offer.get('brand')) or not same_price(actual[6], price)
                    or Decimal(actual[7]) != (Decimal(row[7]) if staged else
                        (Decimal(row[5]) * Decimal(price)).quantize(Decimal('0.01'), rounding='ROUND_HALF_UP'))
                    or actual[8] != source):
                raise ValueError('Target price or source readback differs')
            observed[identity] = actual
        elif actual != row:
            raise ValueError('An unrelated resource row changed')
    return observed


def price_command(state, runtime_root, project_id, *, operation, command_id=None, items=(), check_only=False,
                  assistant_root=None):
    reviewed = [reviewed_price(item) for item in items]
    if operation == 'apply':
        if not command_id or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,92}', command_id):
            raise Rejected('IDENTIFIER_INVALID', 'Provide a bounded price batch command ID')
        if not 1 <= len(reviewed) <= 12 or len({item[0] for item in reviewed}) != len(reviewed):
            raise Rejected('PRICE_BATCH_INVALID', 'Provide 1 to 12 distinct reviewed resource rows')
        if not check_only and len(reviewed) > 8:
            raise Rejected('PRICE_BATCH_INVALID', 'Provide up to eight rows')
        if not check_only:
            for item in items:
                basis = item.get('tax_basis', 'ex_tax')
                try:
                    offer = item['quote']
                    rates = [Decimal(str(offer[key])) for key in ('ex_tax', 'incl_tax')]
                    valid = (basis in ('ex_tax', 'incl_tax') and
                             Decimal(str(offer[basis])) == Decimal(str(item['price'])) and
                             all(value.is_finite() and value > 0 and value.normalize().as_tuple().exponent >= -6
                                 for value in rates))
                except (InvalidOperation, KeyError, TypeError):
                    valid = False
                if not valid:
                    raise Rejected('PRICE_QUOTE_VALUE_CHANGED', 'Use the original quoted price and both tax amounts')
                if offer.get('source') and item['source'] != native_price_source(offer):
                    raise Rejected('PRICE_SOURCE_FORMAT', 'Use the source label and month from the selected quote')
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if not project:
            raise Rejected('PROJECT_NOT_BOUND', 'Bind the current project first')
        with execution_lock(store.root, project['file_id']):
            ctx = SessionContext(**json.loads(project['metadata'])['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            meta = manager.validate_session(ctx)
            if operation != 'controls' and (meta.get('dirty') or store.load_pending(project_id) or NativeSaveAttempts(store).active(project_id)
                    or store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'",
                                        (project_id,)).fetchone()):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve the original pending command before pricing')
            runtime = Path(runtime_root).resolve() / 'price' / uuid.uuid4().hex
            bridge = build_price_bridge(runtime, command_id or ('price-view-' + uuid.uuid4().hex), meta, profile)
            if operation == 'controls':
                return bridge.call('price.controls')
            if operation in ('人材机汇总', '预算书'):
                bridge.call('price.view', expected_target=dict(view=operation))
                capture = bridge.call('inspect')
                if operation == '预算书':
                    return dict(view=operation, count=len(table(capture, '工程量表达式')['rows']), business_dispatches=0)
                rows = summary(capture)
                return dict(view=operation, count=len(rows), headers=SUMMARY_HEADERS, rows=rows, business_dispatches=0)

            initial = bridge.call('inspect')
            baseline = indexed_summary(initial)
            results = []
            targets = {}
            signature = digest(dict(project_id=project_id, items=items))
            prior = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
            if prior:
                if prior['request_digest'] == signature and prior['phase'] == 'readback_verified':
                    return dict(json.loads(prior['result']), receipt_only=True)
                raise Rejected('PRICE_COMMAND_CHANGED', 'Compare the original batch receipt')
            for item, (key, price, source) in zip(items, reviewed):
                candidates = [(identity, row) for identity, row in baseline.items() if identity[:5] == key
                              and matches_current_price(item, row)]
                if len(candidates) != 1:
                    raise Rejected('PRICE_TARGET_CHANGED', 'The reviewed resource must resolve to one row')
                identity, row = candidates[0]
                if Decimal(row[5]) <= 0 or row[9] != 'false':
                    raise Rejected('PRICE_TARGET_CHANGED', 'Exact target must still have positive usage and an unlocked price')
                target = dict(row=row, price=price, source=source, replace='expected_current' in item)
                check = bridge.call('price.check', expected_target=target)
                if check_only:
                    if check_only == 'select':
                        bridge.call('price.select', expected_target=target)
                        selected = bridge.call('inspect')
                        tables = [tab for tab in selected['tables'] if tab['headers'] in SUMMARY_HEADER_VARIANTS]
                        if len(tables) != 1 or tables[0]['current'].get('values') != row:
                            raise Rejected('PRICE_SELECTION_NOT_VERIFIED', 'Expected resource row is not current')
                    results.append(dict(target=key, price_editable=check['price_editable'],
                                        source_editable=check['source_editable']))
                    continue
                if not check['price_editable'] or not check['source_editable']:
                    raise Rejected('PRICE_NOT_EDITABLE', 'Both reviewed market price and source must be editable')
                targets[identity] = item, price, source
            if check_only:
                return dict(status='checked', items=results, save_dispatches=0)

            manager.validate_session(ctx)
            intent = 'intent.' + uuid.uuid4().hex
            proof = store.put_evidence(project_id, ctx.session_id, 'native_price_batch_preflight',
                dict(capture=initial, requests=items, identities=list(targets), intent_id=intent))
            result = dict(command_id=command_id, status='armed_unknown', preflight_evidence_id=proof,
                          runtime=str(runtime))
            with store._transaction():
                store.db.execute('INSERT INTO native_cli_commands VALUES(?,?,?,?,?,?)',
                                 (command_id, project_id, signature, 'armed_unknown', encoded(result), int(time.time()*1000)))
            try:
                changes = {}
                quotes = {key: item['quote'] for key, (item, _, _) in targets.items()}
                with assistant_link(assistant_root, ctx) as link:
                    for identity, (item, price, source) in targets.items():
                        send_assistant_quote(link, bridge, baseline[identity], source, item)
                        changes[identity] = price, source
                staged = bridge.call('inspect')
                verify_price_batch(baseline, indexed_summary(staged), changes, staged=True, quote_metadata=quotes)
                controls = bridge.call('price.controls')
                dispatch = None
                if controls.get('modal') != '确认':
                    budget_view = alternate_price_view(controls)
                    bridge.call('price.view', expected_target=dict(view=budget_view, deferred=True))
                    for _ in range(12):
                        controls = bridge.call('price.controls')
                        if controls.get('modal') == '确认' or (
                                not controls.get('modal') and any(c['text'] == budget_view and c['tip'] == '1'
                                    for c in controls['controls'] if c['class'] == 'QTabBar')):
                            break
                        time.sleep(0.25)
                    else:
                        raise ValueError('Could not verify the application view or price confirmation')
                if controls.get('modal') == '确认':
                    dispatch = bridge.call('price.confirm', intent)
                    wait_for_price_modal(bridge, wanted=None)
                bridge.call('price.view', expected_target=dict(view='人材机汇总'))
                current = bridge.call('inspect')
                observed = verify_price_batch(baseline, indexed_summary(current), changes, quote_metadata=quotes)
                return record_price_batch(store, project_id, ctx, result, current, observed, changes, dispatch, quotes)
            except (ValueError, OSError, RuntimeError, KeyError) as exc:
                result.update(error=str(exc), ipc_records=bridge.records)
                save_result(store, result)
                raise Rejected('NEEDS_RECONCILIATION', 'Check original price command ' + command_id) from exc


def alternate_price_view(controls):
    return '预算书' if any(control['text'] == '预算书' and control['class'] == 'QTabBar'
                         for control in controls['controls']) else '概算汇总'


def wait_for_price_modal(bridge, wanted):
    for _ in range(12):
        if bridge.call('price.controls').get('modal') == wanted:
            return
        time.sleep(0.25)
    raise ValueError('The expected price confirmation state did not appear')


def record_price_batch(store, project_id, ctx, result, capture, observed, changes, dispatch, quotes):
    evidence = store.put_evidence(project_id, ctx.session_id, 'native_price_readback',
        dict(capture=capture, dispatch=dispatch, preflight_evidence_id=result['preflight_evidence_id']))
    result.pop('error', None)
    items = []
    for key, (_, source) in changes.items():
        row = observed[key]
        quote = quotes[key]
        fields = {'source': (source, row[8]), 'supplier': (quote.get('supplier'), row[12]),
                  'brand': (quote.get('brand'), row[13])}
        items.append(dict(row=row, target=key, source=source, quote=quote,
                          metadata_readback={field: actual for field, (_, actual) in fields.items()},
                          metadata_unconfirmed=[field for field, (expected, actual) in fields.items()
                                                if expected and expected != actual]))
    result.update(status='native_price_batch_verified_not_saved', evidence_id=evidence, save_dispatches=0, items=items)
    save_result(store, result, 'readback_verified')
    return result


def reconcile_price(state, command_id, *, refresh_view=False, confirm_staged=False):
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        entry = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
        if not entry or entry['phase'] != 'armed_unknown':
            raise Rejected('PRICE_COMMAND_CHANGED', 'An unresolved original price command is required')
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (entry['project_id'],)).fetchone()
        with execution_lock(store.root, project['file_id']):
            if dict(store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()) != dict(entry):
                raise Rejected('PRICE_COMMAND_CHANGED', 'The original price command changed')
            meta = json.loads(project['metadata'])
            ctx = SessionContext(**meta['session'])
            result = json.loads(entry['result'])
            proof = store.read_evidence(result['preflight_evidence_id'], ctx.project_id)
            before = proof['data']
            if proof['kind'] == 'native_price_batch_preflight':
                if proof['session_id'] != ctx.session_id or digest(dict(project_id=ctx.project_id,
                        items=before['requests'])) != entry['request_digest']:
                    raise Rejected('PRICE_EVIDENCE_MISMATCH', 'The original reviewed batch request is required')
                manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile_path(ctx.profile_id)))
                path = Path(meta['workcopy_path'])
                disk = manager.inspect_saved_open_identity(ctx, file_sha256(path), allow_disabled_window=confirm_staged)
                bridge = load_bridge(Path(result['runtime']))
                baseline = indexed_summary(before['capture'])
                changes = {}
                quotes = {}
                for raw, item in zip(before['identities'], before['requests'], strict=True):
                    identity = tuple(raw)
                    key, price, source = reviewed_price(item)
                    if identity[:5] != key or not matches_current_price(item, baseline[identity]):
                        raise Rejected('PRICE_EVIDENCE_MISMATCH', 'The original reviewed row is required')
                    changes[identity] = price, source
                    quotes[identity] = item['quote']
                if confirm_staged:
                    manager.validate_session(ctx)
                    verify_price_batch(baseline, indexed_summary(bridge.call('inspect')), changes, staged=True,
                                       quote_metadata=quotes)
                    if result.get('confirmation_runtime') or any(
                            json.loads(Path(record).read_text(encoding='utf-8'))['operation'] == 'price.confirm'
                            for record in result.get('ipc_records', [])):
                        raise Rejected('PRICE_COMMIT_ALREADY_ARMED', 'Inspect the original confirmation without replay')
                    result['confirmation_runtime'] = result['runtime']
                    save_result(store, result)
                    controls = bridge.call('price.controls')
                    if controls.get('modal') != '确认':
                        bridge.call('price.view', expected_target=dict(view=alternate_price_view(controls), deferred=True))
                        wait_for_price_modal(bridge, wanted='确认')
                    bridge.call('price.confirm', before['intent_id'])
                    wait_for_price_modal(bridge, wanted=None)
                if refresh_view or confirm_staged:
                    bridge.call('price.view', expected_target=dict(view='人材机汇总'))
                active = bridge.call('inspect')
                if (active['pid'] != ctx.pid or active['created_filetime'] != ctx.created_filetime
                        or not active['callback_on_app_thread']):
                    raise Rejected('PRICE_EVIDENCE_MISMATCH', 'Open summary no longer belongs to the original process')
                if not confirm_staged and manager.inspect_saved_open_identity(ctx, disk['sha256']) != disk:
                    raise Rejected('PRICE_EVIDENCE_MISMATCH', 'Disk changed during price readback')
                snapshot = indexed_summary(active)
                remaining = []
                if refresh_view and not bridge.call('price.controls').get('modal'):
                    remaining = [key for key in changes if snapshot.get(key) == baseline[key]]
                completed = {key: value for key, value in changes.items() if key not in remaining}
                observed = verify_price_batch(baseline, snapshot, completed, quote_metadata=quotes)
                result['remaining_targets'] = remaining
                return record_price_batch(store, ctx.project_id, ctx, result, active, observed, completed, None, quotes)
            if (proof['kind'] != 'native_price_preflight' or proof['session_id'] != ctx.session_id
                    or digest(dict(action_id=command_id, project_id=ctx.project_id, item=before['request'])) != entry['request_digest']):
                raise Rejected('PRICE_EVIDENCE_MISMATCH', 'The original price preflight and request are required')
            key, price, source = reviewed_price(before['request'])
            ipc = [json.loads(Path(path).read_text(encoding='utf-8')) for path in result['ipc_records']]
            operations = [row for row in ipc if row['operation'] in ('price.apply', 'price.confirm')
                          and row['request'].get('outer_intent_id') == before['intent_id']]
            if len(operations) != 1:
                raise Rejected('PRICE_EVIDENCE_MISMATCH', 'One original price dispatch is required')
            original = operations[0]
            dispatch = original['receipt'] or {}
            baseline = indexed_summary(before['capture'])
            candidates = [identity for identity, row in baseline.items() if identity[:5] == key and row[6] == '0']
            if len(candidates) != 1:
                raise Rejected('PRICE_EVIDENCE_MISMATCH', 'The original price target is ambiguous')
            key = candidates[0]
            if (original['operation'] == 'price.apply'
                    and original['request'].get('expected_target') != dict(row=baseline[key], price=price, source=source)):
                raise Rejected('PRICE_EVIDENCE_MISMATCH', 'Original target does not match the reviewed quote')
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile_path(ctx.profile_id)))
            file = Path(meta['workcopy_path'])
            def observe_disk():
                sha256 = file_sha256(file)
                if not confirm_staged:
                    return manager.inspect_saved_open_identity(ctx, sha256)
                manager.validate_session(ctx)
                if sha256 != meta['initial_sha256']:
                    raise Rejected('PRICE_EVIDENCE_MISMATCH', 'Original workcopy changed before confirmation')
                return dict(sha256=sha256)

            disk = observe_disk()
            observer = load_bridge(Path(result['runtime']))
            if refresh_view:
                runtime = Path(result['runtime']).parent / uuid.uuid4().hex
                observer = build_price_bridge(runtime, command_id, meta, profile_path(ctx.profile_id))
                controls = observer.call('price.controls')['controls']
                budget_view = '预算书' if any(control['text'] == '预算书' and control['class'] == 'QTabBar'
                                             for control in controls) else '概算汇总'
                observer.call('price.view', expected_target=dict(view=budget_view))
                observer.call('price.view', expected_target=dict(view='人材机汇总'))
            active = observer.call('inspect')
            if (active['pid'] != ctx.pid or active['created_filetime'] != ctx.created_filetime
                    or not active['callback_on_app_thread']
                    or observe_disk() != disk):
                raise Rejected('PRICE_EVIDENCE_MISMATCH', 'Original project changed during price readback')
            current = indexed_summary(active)
            if confirm_staged:
                verify_price_delta(baseline, current, key, price, source, True, staged=True)
                if result.get('confirmation_runtime'):
                    raise Rejected('PRICE_COMMIT_ALREADY_ARMED', 'Inspect the original confirmation without replay')
                runtime = Path(result['runtime']).parent / uuid.uuid4().hex
                bridge = build_price_bridge(runtime, command_id, meta, profile_path(ctx.profile_id))
                result['confirmation_runtime'] = str(runtime)
                save_result(store, result)
                bridge.call('price.confirm', 'intent.' + uuid.uuid4().hex)
                bridge.call('price.view', expected_target=dict(view='人材机汇总'))
                active = bridge.call('inspect')
                current = indexed_summary(active)
            if ((dispatch.get('status') == 'blocked' and dispatch.get('business_dispatches') == 0
                 or refresh_view and disk['sha256'] == meta['initial_sha256']) and current == baseline):
                status, phase = 'native_price_no_dispatch_verified', 'no_dispatch_verified'
                new = current[key]
            else:
                new = verify_price_delta(baseline, current, key, price, source, True)
                status, phase = 'native_price_verified_not_saved', 'readback_verified'
            ref = store.put_evidence(ctx.project_id, ctx.session_id, 'native_price_readback',
                                    dict(capture=active, original=original, source=source, observed_disk_identity=disk))
            result.update(status=status, row=new, evidence_id=ref, source=source,
                          readonly_reconciled=True, new_business_dispatches=0)
            save_result(store, result, phase)
            return result
