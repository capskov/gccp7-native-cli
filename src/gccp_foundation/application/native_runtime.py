"""Prepare scoped native workers from the proven bridge, before any business dispatch.

This internal version deliberately supports one demonstrated quota family. It
generates fixed contracts, never an arbitrary function/QObject invocation API.
"""
import ast
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from gccp_foundation.application.native_cli import Catalog, Rejected, runtime_config
from gccp_foundation.application.native_worker_support import project_window_prefix, table
from gccp_foundation.application.native_build import TEMPLATES, compile_bridge, profile_path
from gccp_foundation.storage import Ledger
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


PIPE_SPECS = dict(zip(range(193, 209), [100,150,200,250,300,400,500,600,700,800,900,1000,1200,1400,1600,1800]))


def reviewed_family(entry, quantity):
    try:
        q = Decimal(str(quantity))
    except InvalidOperation as exc:
        raise Rejected('QUANTITY_INVALID', 'Finite positive quantity required') from exc
    if not q.is_finite() or q <= 0 or q > 1000000 or q.as_tuple().exponent < -4:
        raise Rejected('QUANTITY_NOT_REVIEWED', 'This internal worker accepts positive quantities up to 1000000 with at most four decimal places; insertion itself starts at zero')
    match = re.fullmatch(r'3-(\d+)', entry.get('code') or '')
    spec = PIPE_SPECS.get(int(match[1])) if match else None
    if (entry['source_filename'] != '04市政第四册上.pdf'
            or entry['name'] != '球墨铸铁管管件安装' or entry['unit'] != '个'
            or spec is None or entry['spec'] != str(spec)):
        raise Rejected('FAMILY_NOT_REVIEWED', 'Runtime preparation is limited to the native-checked Beijing2025 pipe-fitting family 3-193..3-208; other entries remain search-only')
    value = format(q, 'f')
    if '.' in value:
        value = value.rstrip('0').rstrip('.')
    return dict(code=entry['code'], name=f'球墨铸铁管管件安装 公称直径(mm以内) {spec}',
                unit='个', specialty='管网'), value


def _replace(text, before, after):
    if before not in text:
        raise Rejected('TEMPLATE_CHANGED', 'Reviewed runtime template no longer matches its renderer')
    return text.replace(before, after)


def render(templates, directory, *, entry, identity, quantity, request, session, profile,
           title_prefix, owner, budget_count, parent_name, parent_path, anchor_values, child_count):
    """Pure rendering of one fixed command contract; no process or file writes."""
    cpp = lambda value: json.dumps(value, ensure_ascii=False)
    blank = not anchor_values[0]
    anchor = child_count - 1
    inserted = anchor if blank else child_count
    label = ' [CLI:' + hashlib.sha256(request['command_id'].encode()).hexdigest()[:8] + ']'
    expected = 'QJsonArray{' + ','.join(cpp(v) for v in anchor_values) + '}'
    h = (templates/'operations.h.in').read_text(encoding='utf-8')
    h = h.replace('3-200', identity['code']).replace('球墨铸铁管管件安装 公称直径(mm以内) 600', identity['name']).replace('[CLI v0.1测试]', label.strip())
    h = h.replace('"管件安装"', cpp(parent_name)).replace('"BZ010"', cpp(request['parent_id']))
    a = h.index('int blanks=0;', h.index('if(op=="prepare")'))
    z = h.index('auto target=', a)
    h = h[:a] + f'''int blanks=0;
        if(bm->rowCount(parent)!={child_count} || rowValues(bm,bm->index({anchor},0,parent))!={expected}){{error="reviewed_parent_context_changed";return false;}}
        s.row={anchor};
        ''' + h[z:]
    h = _replace(h, 'current.sibling(current.row(),0)!=target || !targetValues[0].toString().isEmpty()',
                 'current.sibling(current.row(),0)!=target || targetValues!=' + expected)
    a = h.index('    if(op=="bind.inserted"){'); z = h.index('    if(s.row<0 ||', a)
    h = h[:a] + f'''    if(op=="bind.inserted"){{
        if(!s.inserted || bm->rowCount(parent)!={child_count if blank else child_count+1}){{error="inserted_row_count_changed";return false;}}
        auto i=bm->index({inserted},0,parent);auto v=rowValues(bm,i);
        if(v[0]!={cpp(identity['code'])} || v[2]!=quotaName() || v[6]!="0"){{error="new_inserted_slot_not_verified";return false;}}
        s.row={inserted};out["bound_inserted_row"]=s.row;return true;
    }}
''' + h[z:]
    h = h.replace('v[6]=="1"', 'v[6]==' + cpp(quantity)).replace('targetValues[6]!="1"', 'targetValues[6]!=' + cpp(quantity))
    h = _replace(h, 'QString value=zero?QStringLiteral("0"):QStringLiteral("1")',
                 'QString value=zero?QStringLiteral("0"):QStringLiteral(' + cpp(quantity) + ')')
    transport = render_transport(templates, directory, request['command_id'], session, profile, title_prefix)
    worker = (templates/'worker.py.in').read_text(encoding='utf-8')
    worker = worker.replace('bridge_client_internal_v01_q', 'cli_bridge').replace('3-200', identity['code']).replace('3200', hashlib.sha256(request['command_id'].encode()).hexdigest()[:12])
    worker = worker.replace("NAME='球墨铸铁管管件安装 公称直径(mm以内) 600'", 'NAME=' + repr(identity['name']))
    worker = worker.replace('internal-v01-q-', 'command-')
    worker = worker.replace("assert len(budget['rows'])==71", f"assert len(budget['rows'])=={budget_count}")
    worker = worker.replace("  assert not any(r['values'][0]=='" + identity['code'] + "' for r in budget['rows'])", "  assert not any(r['values'][2]==NAME+LABEL for r in budget['rows'])")
    worker = worker.replace("target=[r for r in zb['rows'] if r['values'][0]=='" + identity['code'] + "']", "target=[r for r in zb['rows'] if r['values'][0]=='" + identity['code'] + "' and r['values'][2]==NAME+LABEL]")
    worker = worker.replace("value='1'", 'value=' + repr(quantity)).replace("v[6]=='1'", 'v[6]==' + repr(quantity)).replace("['values'][6]=='1'", "['values'][6]==" + repr(quantity))
    worker = worker.replace("quantity='1'", 'quantity=' + repr(quantity))
    worker = _replace(worker, '   normalized[path]=original[path]', "   if path in original:normalized[path]=original[path]")
    worker = worker.replace("parts[1:3]==['0','2']", "parts[1:3]==" + repr(parent_path.strip('/').split('/')))
    imports = ('\nSTATE=Path(' + repr(request['_state']) + ')\nPROJECT=' + repr(request['project_id']) +
               '\nEXPECTED_OWNER=' + repr(owner) + '\nPARENT_ID=' + repr(request['parent_id']) + '\nLABEL=' + repr(label) + '\n')
    worker = worker.replace('\ndef main():', imports + '\ndef main():', 1)
    ast.parse(worker)
    return {**transport, 'operations.h':h, 'worker.py':worker}


def render_transport(templates, directory, command_id, session, profile, title_prefix):
    """Render the same session-pinned file IPC transport for each native operation."""
    cpp = lambda value: json.dumps(value, ensure_ascii=False)
    bridge = (templates/'bridge.cpp.in').read_text(encoding='utf-8')
    bridge = _replace(bridge, '"__IPC_DIRECTORY__"', cpp((directory/'build').as_posix() + '/'))
    bridge = _replace(bridge, '__COMMAND_ID__', command_id)
    client = (templates/'client.py.in').read_text(encoding='utf-8')
    substitutions = {
        '__PID__': str(session['pid']), '__BIRTH__': repr(session['created_filetime']),
        '__HWND__': str(session['hwnd']), '__COMMAND_ID__': repr(command_id),
        '__PROFILE__': repr(str(profile)), '__PROJECT_TITLE_PREFIX__': repr(title_prefix),
    }
    for marker, value in substitutions.items():
        client = _replace(client, marker, value)
    ast.parse(client)
    files = {n:(templates/n).read_text(encoding='utf-8')
             for n in ('build.ps1', 'export_current_index.h', 'current_selection.h')}
    return {**files, 'bridge.cpp':bridge, 'cli_bridge.py':client}


def prepare_entry(catalog_path, state, inspector_path, project_id, parent_id, quantity, command_id, entry_id, output_root):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', command_id):
        raise Rejected('COMMAND_ID_INVALID', 'Use a bounded ASCII business command identifier')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', parent_id):
        raise Rejected('PARENT_ID_INVALID', 'An explicit parent business identifier is required')
    catalog = Catalog(catalog_path)
    try:
        entry = catalog.entry(entry_id)
    finally:
        catalog.close()
    identity, quantity = reviewed_family(entry, quantity)
    request = dict(command_id=command_id, project_id=project_id, entry_id=entry_id,
                   parent_id=parent_id, quantity=quantity, save=True)
    directory = Path(output_root).resolve() / 'commands' / hashlib.sha256(command_id.encode()).hexdigest()[:20]
    manifest = directory/'runtime.json'
    if manifest.exists():
        config, _ = runtime_config(manifest)
        if config['approved_request'] != request:
            raise Rejected('IDEMPOTENCY_CONFLICT', 'Prepared command has different business parameters')
        return manifest, directory/'request.json', request
    preflight = Ledger(state)
    try:
        row = preflight.db.execute('SELECT metadata FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if row is None:
            raise Rejected('PROJECT_NOT_BOUND', 'Register the current project first')
        if preflight.load_pending(project_id) or json.loads(row['metadata']).get('dirty') or NativeSaveAttempts(preflight).active(project_id):
            raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'An unresolved action or Save blocks preparation; do not issue a second native call')
        if preflight.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_cli_commands'").fetchone():
            if preflight.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'", (project_id,)).fetchone():
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve the prior CLI command before preparing another')
    finally:
        preflight.close()
    _, inspector = runtime_config(inspector_path)
    observed = subprocess.run([sys.executable, '-X', 'utf8', str(inspector), '--inspect'],
        capture_output=True, text=True, encoding='utf-8', check=True, timeout=120)
    capture = json.loads(observed.stdout); budget = table(capture, '工程量表达式')
    store = Ledger(state)
    try:
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if not project:
            raise Rejected('PROJECT_NOT_BOUND', 'Current project must already be registered')
        meta = json.loads(project['metadata']); session = meta['session']
        if (store.load_pending(project_id) or meta.get('dirty')
                or capture.get('pid') != session['pid'] or capture.get('created_filetime') != session['created_filetime']
                or capture.get('callback_on_app_thread') is not True):
            raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Fresh same-process model without unknown effects is required')
        if project['owner'] and any(store.action_state(project['owner'], a) != 'readback_verified' for a in ('insert','quantity')):
            raise Rejected('OWNER_NOT_REVIEWED', 'Previous business phase must be readback-verified')
        parents = [r for r in budget['rows'] if r['values'][1] == '部' and r['values'][12] == parent_id]
        if len(parents) != 1 or parents[0]['depth'] != 1:
            raise Rejected('PARENT_NOT_UNIQUE', 'A unique top-level budget parent is required')
        parent = parents[0]
        children = [r for r in budget['rows'] if r['row_path_session_only'].rsplit('/',1)[0] == parent['row_path_session_only']]
        children.sort(key=lambda r:int(r['row_path_session_only'].rsplit('/',1)[1]))
        if not 1 <= len(children) <= 99:
            raise Rejected('PARENT_SHAPE_NOT_REVIEWED', 'Non-empty bounded parent required by this internal worker')
        directory.mkdir(parents=True, exist_ok=False)
        generated = render(TEMPLATES, directory, entry=entry, identity=identity,
            quantity=quantity, request={**request, '_state':str(Path(state).resolve())}, session=session,
            profile=profile_path(session['profile_id']),
            title_prefix=project_window_prefix(meta['workcopy_path']), owner=project['owner'],
            budget_count=len(budget['rows']), parent_name=parent['values'][2],
            parent_path=parent['row_path_session_only'], anchor_values=children[-1]['values'], child_count=len(children))
        for name, text in generated.items():
            (directory/name).write_text(text, encoding='utf-8', newline='\n')
        (directory/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (directory/'preparation.json').write_text(json.dumps(dict(catalog_entry=entry,capture=capture,
            session=session,owner=project['owner'],business_dispatches=0),ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        store.close()
    compile_bridge(directory)
    worker = directory/'worker.py'
    pins = {str(directory/name):hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in generated}
    binary = directory/'build/gccp_business.dll'
    pins[str(binary)] = hashlib.sha256(binary.read_bytes()).hexdigest()
    config = dict(channel='native_qt_business', live_candidate_check_required=True, worker=str(worker),
        worker_sha256=pins[str(worker)], artifact_sha256=pins, catalog_identity_digest=entry['identity_digest'],
        expected_live_identity=identity, approved_request=request, catalog_match='live_reviewed_family',
        scope='Beijing2025 pipe fittings 3-193..3-208, positive quantity, current pinned process, no copy/reopen')
    manifest.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest, directory/'request.json', request
