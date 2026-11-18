"""Prepare scoped native workers from the proven bridge, before any business dispatch.

Each contract pins one exact live candidate, whether supplied by the operator
or derived from the demonstrated family.
"""
import ast
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from gccp_foundation.application.native_cli import (
    DEFAULT_SAVE_TIMEOUT, Rejected, request_catalog_entry, require_quota_execution_scope, runtime_config,
    save_timeout_seconds,
)
from gccp_foundation.application.native_worker_support import project_window_prefix, table
from gccp_foundation.application.native_build import TEMPLATES, compile_bridge, profile_path
from gccp_foundation.storage import Ledger
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


PIPE_SPECS = dict(zip(range(193, 209), [100,150,200,250,300,400,500,600,700,800,900,1000,1200,1400,1600,1800]))
MAX_ANCHOR_STEPS = 32


def normalize_quantity(quantity, *, allow_zero=False):
    try:
        q = Decimal(str(quantity))
    except InvalidOperation as exc:
        raise Rejected('QUANTITY_INVALID', 'Finite positive quantity required') from exc
    if not q.is_finite() or q < 0 or (q == 0 and not allow_zero) or q > 1000000 or q.as_tuple().exponent < -4:
        minimum = 'zero' if allow_zero else 'a positive number'
        raise Rejected('QUANTITY_NOT_REVIEWED', f'Quantity must be {minimum} through 1000000, with at most four decimal places')
    if q == 0:
        return '0'
    value = format(q, 'f')
    if '.' in value:
        value = value.rstrip('0').rstrip('.')
    return value


def displayed_quantity(unit, quantity):
    if unit not in ('m', 'm3'):
        return quantity
    shown = Decimal(quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if shown == 0:
        raise Rejected('QUANTITY_BELOW_DISPLAY_PRECISION', 'The quantity would round to zero in the displayed field')
    return normalize_quantity(shown)


def reviewed_family(entry, quantity):
    value = normalize_quantity(quantity)
    match = re.fullmatch(r'3-(\d+)', entry.get('code') or '')
    spec = PIPE_SPECS.get(int(match[1])) if match else None
    if (entry['source_filename'] != '04市政第四册上.pdf'
            or entry['name'] != '球墨铸铁管管件安装' or entry['unit'] != '个'
            or spec is None or entry['spec'] != str(spec)):
        raise Rejected('FAMILY_NOT_REVIEWED', 'Runtime preparation is limited to the native-checked Beijing2025 pipe-fitting family 3-193..3-208; other entries remain search-only')
    return dict(code=entry['code'], name=f'球墨铸铁管管件安装 公称直径(mm以内) {spec}',
                unit='个', specialty='管网'), value


def candidate_identity(entry, quantity, *, name=None, specialty=None):
    if name is None and specialty is None:
        return reviewed_family(entry, quantity)
    fields = dict(code=entry.get('code'), name=name, unit=entry.get('unit'), specialty=specialty)
    for value in fields.values():
        if (not isinstance(value, str) or not value or value != value.strip()
                or len(value) > 240 or any(ord(ch) < 32 for ch in value)):
            raise Rejected('CANDIDATE_IDENTITY_REQUIRED', 'Supply the exact GCCP name and specialty with catalog code and unit')
    return fields, normalize_quantity(quantity)


def _replace(text, before, after):
    if before not in text:
        raise Rejected('TEMPLATE_CHANGED', 'Reviewed runtime template no longer matches its renderer')
    return text.replace(before, after)


def find_section_path(budget_rows, sections):
    path = '/0'
    for depth, name in enumerate(sections, start=1):
        matches = [row for row in budget_rows
                   if row['depth'] == depth
                   and row['row_path_session_only'].rsplit('/', 1)[0] == path
                   and row['values'][1:3] == ['部', name]]
        if len(matches) != 1:
            raise Rejected('PARENT_NOT_UNIQUE', f'Section {name!r} must exist once under the specified parent')
        parent = matches[0]
        path = parent['row_path_session_only']
    return parent


def is_blank_quota(values):
    return (values[0:4] == ['', '定', '', '']
            and all(value in ('', '0') for value in values[4:]))


def bounded_anchor_route(rows, cursor, anchor):
    if not cursor.get('verified'):
        return []
    start = cursor['row_path_session_only']
    end = anchor['row_path_session_only']
    scope = '/' + '/'.join(end.split('/')[1:3]) + '/'
    positions = {row['row_path_session_only']: i for i, row in enumerate(rows)}
    if start not in positions or not start.startswith(scope):
        return []
    first, last = positions[start], positions[end]
    if rows[first]['values'] != cursor['values'] or not 1 <= abs(last - first) <= MAX_ANCHOR_STEPS:
        return []
    direction = 1 if last > first else -1
    route = [rows[i] for i in range(first, last + direction, direction)]
    if any(not row['row_path_session_only'].startswith(scope) or row['values'][1] not in ('部', '定', '换', '补', '主')
           for row in route):
        return []
    return route


def render(templates, directory, *, entry, identity, quantity, request, session, profile,
           title_prefix, owner, budget_count, parent_name, parent_path, anchor_values, child_count,
           preceding_anchor_values=None, navigation_rows=(), direct_anchor=False):
    """Pure rendering of one fixed command contract; no process or file writes."""
    cpp = lambda value: json.dumps(value, ensure_ascii=False)
    blank = not anchor_values[0]
    anchor = child_count - 1
    inserted = anchor if blank else child_count
    item_label = request.get('item_label')
    scope_label = f' [用途:{item_label}]' if item_label else ''
    label = scope_label + ' [CLI:' + hashlib.sha256(request['command_id'].encode()).hexdigest()[:8] + ']'
    expected = 'QJsonArray{' + ','.join(cpp(v) for v in anchor_values) + '}'
    display = displayed_quantity(identity['unit'], quantity)
    h = (templates/'operations.h.in').read_text(encoding='utf-8')
    family = identity['name'].split()[0]
    if request.get('selected_anchor') and len(identity['code']) <= 3 and len(family) >= 4:
        h = _replace(h, 'edits[0]->setText(__CANDIDATE_CODE__);',
                     'edits[0]->setText(QStringLiteral(' + cpp(family) + '));')
    h = h.replace('[CLI v0.1测试]', label.strip())
    if sections := request.get('parent_sections'):
        start = h.index('    auto project=bm->index(0,0);QModelIndex parent;')
        end = h.index('    auto bv=', start)
        slots = ','.join(parent_path.split('/')[2:])
        names = ','.join(cpp(name) for name in sections)
        h = h[:start] + f'''    QModelIndex parent=bm->index(0,0);
    const QStringList sectionNames{{{names}}};
    const int sectionRows[]={{ {slots} }};
    for(int depth=0;depth<sectionNames.size();++depth){{
        const int count=bm->rowCount(parent);
        if(count>100){{error="division_bound";return false;}}
        QModelIndex match;int matches=0;
        for(int r=0;r<count;++r){{
            const auto idx=bm->index(r,0,parent);const auto v=rowValues(bm,idx);
            if(v[1]=="部" && v[2]==sectionNames[depth]){{match=idx;++matches;}}
        }}
        if(matches!=1 || match.row()!=sectionRows[depth]){{error="section_path_changed";return false;}}
        parent=match;
    }}
    if(bm->rowCount(parent)>100){{error="division_bound";return false;}}
''' + h[end:]
    else:
        h = h.replace('"管件安装"', cpp(parent_name)).replace('"BZ010"', cpp(request['parent_id']))
    a = h.index('int blanks=0;', h.index('if(op=="prepare")'))
    z = h.index('auto target=', a)
    h = h[:a] + f'''int blanks=0;
        if(bm->rowCount(parent)!={child_count} || rowValues(bm,bm->index({anchor},0,parent))!={expected}){{error="reviewed_parent_context_changed";return false;}}
        s.row={anchor};
        ''' + h[z:]
    if request.get('parent_sections'):
        start = h.index('        int n=budget->metaObject()->indexOfMethod', h.index('if(op=="prepare")'))
        end = h.index('    if(op=="resume.done")', start)
        h = h[:start] + '''        auto selected=readPinnedCurrentIndex(bv,reinterpret_cast<const void*>(cg));
        if(!target.isValid() || target.model()!=bm || !selected.isValid() ||
            selected.sibling(selected.row(),0)!=target){
            error="section_anchor_not_selected";return false;
        }
        out["target_child_row"]=s.row;
        out["selected_anchor_verified"]=true;
        return true;
    }
''' + h[end:]
        h = _replace(h, '"quantity.normal.one","resources.key.up"}.contains(op)',
                     '"quantity.normal.one","resources.key.up","resources.show"}.contains(op)')
        start = h.index('        auto row=bm->index(s.row,0,parent);int nav=', h.index('if(op=="resources.show")'))
        end = h.index('        QTabBar *bar=', start)
        h = h[:start] + '''        const auto selected=readPinnedCurrentIndex(bv,reinterpret_cast<const void*>(cg));
        if(!selected.isValid() || selected.sibling(selected.row(),0)!=target || !s.quantityDone){
            error="resource_owner_not_selected";return false;
        }
''' + h[end:]
    if request.get('selected_anchor'):
        if direct_anchor:
            h = _replace(h, '        if(!target.isValid() || target.model()!=bm || !selected.isValid()',
                         f'''        if(!prepareStage(req,"anchor-select-before",error))return false;
        const QPointer<QWidget> guardedBudget(budget);
        const QPointer<QAbstractItemModel> guardedModel(bm);
        const QPersistentModelIndex expectedTarget(target);
        const auto meta=budget->metaObject();
        const int nav=meta->indexOfMethod("setCurrentIndex(QModelIndex)");
        if(nav<0 || !expectedTarget.isValid() ||
            QString(meta->method(nav).enclosingMetaObject()->className())!="GSPTableView" ||
            !meta->method(nav).invoke(budget,Qt::DirectConnection,Q_ARG(QModelIndex,target))){{
            error="anchor_navigation_unavailable";return false;
        }}
        out["application_selection_changed"]=true;
        if(!prepareStage(req,"anchor-select-after",error))return false;
        if(!guardedBudget || !guardedModel || !expectedTarget.isValid() ||
            mg(bv)!=guardedModel.data() || rowValues(bm,expectedTarget)!={expected}){{
            error="anchor_navigation_model_changed";return false;
        }}
        selected=readPinnedCurrentIndex(bv,reinterpret_cast<const void*>(cg));
        if(!target.isValid() || target.model()!=bm || !selected.isValid()''')
        if navigation_rows:
            route = ',\n            '.join(
                'QJsonObject{{"path",' + cpp(row['row_path_session_only']) +
                '},{"values",QJsonArray{' + ','.join(cpp(v) for v in row['values']) + '}}}'
                for row in navigation_rows)
            first_path = tuple(map(int, navigation_rows[0]['row_path_session_only'].strip('/').split('/')))
            last_path = tuple(map(int, navigation_rows[-1]['row_path_session_only'].strip('/').split('/')))
            key = 'Qt::Key_Down' if last_path > first_path else 'Qt::Key_Up'
            h = _replace(h, '        if(!target.isValid() || target.model()!=bm || !selected.isValid()',
                         f'''        const QList<QJsonObject> route{{
            {route}
        }};
        QPointer<QWidget> guardedBudget(budget);
        QPointer<QAbstractItemModel> guardedModel(bm);
        QPersistentModelIndex expectedTarget(target);
        const auto start=currentIndexEvidence(bm,selected);
        if(!start["verified"].toBool() || start["row_path_session_only"]!=route.first()["path"]
            || start["values"]!=route.first()["values"] || !expectedTarget.isValid()
            || !parent.isValid() || parent.model()!=bm){{
            error="anchor_navigation_step_mismatch";return false;
        }}
        // Exact x64 export from the fingerprinted GLDTableView library.
        using ExpandSection=void(*)(void*,const QModelIndex&,bool);
        auto view=budget->qt_metacast("GlodonTableView");
        auto expand=reinterpret_cast<ExpandSection>(GetProcAddress(mod,"?expand@GlodonTableView@@QEAAXAEBVQModelIndex@@_N@Z"));
        if(!view || !expand){{error="section_expand_export_missing";return false;}}
        if(!prepareStage(req,"anchor-expand-before",error))return false;
        expand(view,parent,false);
        if(!prepareStage(req,"anchor-expand-after",error))return false;
        if(!guardedBudget || !guardedModel || !expectedTarget.isValid() ||
            mg(bv)!=guardedModel.data() || rowValues(bm,expectedTarget)!={expected}){{
            error="anchor_navigation_model_changed";return false;
        }}
        selected=readPinnedCurrentIndex(bv,reinterpret_cast<const void*>(cg));
        int moves=0;
        for(int step=0;step<route.size();++step){{
            const auto observed=currentIndexEvidence(bm,selected);
            // A collapsed quota can hide its material children from keyboard navigation.
            // Only skip recorded material rows, then still require the exact next budget row.
            while(step+1<route.size() && route[step]["path"]!=observed["row_path_session_only"]
                && route[step]["values"].toArray().at(1)=="主")++step;
            if(!observed["verified"].toBool() ||
                observed["row_path_session_only"]!=route[step]["path"] ||
                observed["values"]!=route[step]["values"]){{
                out["anchor_observed"]=observed;
                error="anchor_navigation_step_mismatch";return false;
            }}
            if(step+1==route.size())break;
            const auto stage=QByteArray("anchor-")+QByteArray::number(++moves);
            if(!prepareStage(req,(stage+"-before").constData(),error))return false;
            QKeyEvent move(QEvent::KeyPress,{key},Qt::NoModifier);
            QApplication::sendEvent(budget,&move);
            out["application_selection_changed"]=true;
            if(!prepareStage(req,(stage+"-after").constData(),error))return false;
            if(!guardedBudget || !guardedModel || !expectedTarget.isValid() ||
                mg(bv)!=guardedModel.data() || rowValues(bm,expectedTarget)!={expected}){{
                error="anchor_navigation_model_changed";return false;
            }}
            selected=readPinnedCurrentIndex(bv,reinterpret_cast<const void*>(cg));
        }}
        out["anchor_navigation_steps"]=moves;
        if(!target.isValid() || target.model()!=bm || !selected.isValid()''')
        if preceding_anchor_values is not None:
            preceding = 'QJsonArray{' + ','.join(cpp(v) for v in preceding_anchor_values) + '}'
            h = _replace(h, '        if(!target.isValid() || target.model()!=bm || !selected.isValid()',
                         f'''        if(target.isValid() && target.model()==bm && selected.isValid()
            && selected.model()==bm && selected.parent()==parent && selected.row()=={anchor - 1}
            && rowValues(bm,selected.sibling(selected.row(),0))=={preceding}){{
            if(!prepareStage(req,"02-before-blank-down",error))return false;
            QPointer<QWidget> guardedBudget(budget);
            QPointer<QAbstractItemModel> guardedModel(bm);
            QPersistentModelIndex expectedTarget(target);
            QKeyEvent down(QEvent::KeyPress,Qt::Key_Down,Qt::NoModifier);
            QApplication::sendEvent(budget,&down);
            out["application_selection_changed"]=true;
            if(!prepareStage(req,"03-after-blank-down",error))return false;
            if(!guardedBudget || !guardedModel || !expectedTarget.isValid() ||
                mg(bv)!=guardedModel.data() || rowValues(bm,expectedTarget)!={expected}){{
                error="blank_anchor_model_changed";return false;
            }}
            selected=readPinnedCurrentIndex(bv,reinterpret_cast<const void*>(cg));
            out["blank_anchor_down_dispatched"]=true;
        }}
        if(!target.isValid() || target.model()!=bm || !selected.isValid()''')
        h = _replace(h, '        out["selected_anchor_verified"]=true;', '''        int resourceTabs=0;
        for(auto w:budget->window()->findChildren<QTabBar*>())if(w->isVisible())
            for(int i=0;i<w->count();++i)if(w->tabText(i)=="工料机显示")++resourceTabs;
        if(resourceTabs!=1){error="resource_tab_not_unique";return false;}
        out["selected_anchor_verified"]=true;''')
        start = h.index('        budget->window()->activateWindow();', h.index('if(op=="quantity.normal.zero"'))
        end = h.index('        if(edit<0', start)
        h = h[:start] + '''        const auto selected=readPinnedCurrentIndex(bv,reinterpret_cast<const void*>(cg));
        if(selected!=cell){error="quantity_cell_not_selected_by_insert";return false;}
        const int edit=budget->metaObject()->indexOfMethod("edit(QModelIndex)");
        bool opened=false;
        if(edit<0 || budget->metaObject()->method(edit).returnType()!=QMetaType::Bool){
            error="native_quantity_editor_signature_changed";return false;
        }
''' + h[end:]
        h = _replace(h, 'for(auto w:QApplication::allWidgets())if(auto b=qobject_cast<QTabBar*>(w))if(b->isVisible())',
                     'for(auto b:budget->window()->findChildren<QTabBar*>())if(b->isVisible())')
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
    h = h.replace('v[6]=="1"', 'v[6]==' + cpp(display)).replace('targetValues[6]!="1"', 'targetValues[6]!=' + cpp(display))
    h = _replace(h, 'QString value=zero?QStringLiteral("0"):QStringLiteral("1")',
                 'QString value=zero?QStringLiteral("0"):QStringLiteral(' + cpp(quantity) + ')')
    if request.get('selected_anchor'):
        start = h.index('        auto cell=bm->index(s.row,6,parent);', h.index('    if(op=="quantity.normal.zero"'))
        end = h.index('\n    if(op=="name.set"', start)
        h = h[:start] + '''        const auto cell=bm->index(s.row,4,parent);
        const auto selected=readPinnedCurrentIndex(bv,reinterpret_cast<const void*>(cg));
        if(selected!=bm->index(s.row,6,parent)){error="quantity_cell_not_selected_by_insert";return false;}
        if(!(bm->flags(cell)&Qt::ItemIsEditable)){error="normal_quantity_not_editable";return false;}
        QString value=zero?QStringLiteral("0"):QStringLiteral(''' + cpp(quantity) + ''');
        phase=zero?1:2;s.quantityDone=!zero;
        out["quantity_field_write_dispatches"]=1;
        out["business_dispatches"]=1;
        if(!bm->setData(cell,value,Qt::EditRole)){error="normal_quantity_model_write_rejected";return false;}
        return true;
    }
''' + h[end:]
    transport = render_transport(templates, directory, request['command_id'], session, profile, title_prefix)
    worker = (templates/'worker.py.in').read_text(encoding='utf-8')
    worker = worker.replace('bridge_client_internal_v01_q', 'cli_bridge').replace('3200', hashlib.sha256(request['command_id'].encode()).hexdigest()[:12])
    worker = worker.replace('internal-v01-q-', 'command-')
    worker = worker.replace("assert len(budget['rows'])==71", f"assert len(budget['rows'])=={budget_count}")
    worker = worker.replace("value='1'", 'value=' + repr(quantity)).replace("v[6]=='1'", 'v[6]==' + repr(display)).replace("['values'][6]=='1'", "['values'][6]==" + repr(display))
    if display != quantity:
        worker = _replace(worker, 'assert v[0]==CODE and v[6]==' + repr(display),
                          'assert v[0]==CODE and v[6]==' + repr(display) + ' and v[4]==' + repr(quantity))
    worker = worker.replace("quantity='1'", 'quantity=' + repr(quantity))
    if request.get('selected_anchor'):
        worker = _replace(worker, "   after=b.call('inspect',timeout=90);write_new(ROOT/'command-final-capture.json',after)",
                          "   b.call('resources.show',timeout=90)\n"
                          "   after=b.call('inspect',timeout=90);write_new(ROOT/'command-final-capture.json',after)")
    save_timeout = save_timeout_seconds(request.get('save_timeout', DEFAULT_SAVE_TIMEOUT))
    worker = _replace(worker, 'wait_for_saved_workcopy(file_path,prior)',
                      f'wait_for_saved_workcopy(file_path,prior,timeout={save_timeout})')
    worker = _replace(worker, '   normalized[path]=original[path]', "   if path in original:normalized[path]=original[path]")
    worker = worker.replace("parts[1:3]==['0','2']", "parts[1:3]==" + repr(parent_path.strip('/').split('/')))
    imports = ('\nSTATE=Path(' + repr(request['_state']) + ')\nPROJECT=' + repr(request['project_id']) +
               '\nEXPECTED_OWNER=' + repr(owner) + '\nPARENT_ID=' + repr(request['parent_id']) + '\nLABEL=' + repr(label) +
               '\nTARGET_PATH=' + repr(f'{parent_path}/{inserted}') + '\nSAVE=' + repr(request.get('save', True)) + '\n')
    worker = worker.replace('\ndef main():', imports + '\ndef main():', 1)
    pattern = re.compile(r'__CANDIDATE_(CODE|NAME|UNIT|SPECIALTY)__')
    h = pattern.sub(lambda m: cpp(identity[m[1].lower()]), h)
    worker = pattern.sub(lambda m: repr(identity[m[1].lower()]), worker)
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


def resolve_vendor_identity(state, output_root, project_id, library_root, vendor_id, expected_unit, approved):
    from .vendor_catalog import default_library_root, match_live_entry, vendor_entry

    root = library_root.resolve() if library_root is not None else None
    lookup_runtime = None
    if approved is None:
        from .native_lookup import lookup_vendor

        vendor = vendor_entry(root, vendor_id)
        if root is not None:
            vendor['library_root'] = str(root)
        live = lookup_vendor(state, output_root, project_id, vendor)
        unit = match_live_entry(vendor, live)['unit']
        lookup_runtime = live['runtime']
    else:
        vendor = approved.get('vendor_entry', {})
        if root is None and 'source' not in vendor:
            root = default_library_root().resolve()
        if vendor.get('vendor_id') != vendor_id or vendor.get('library_root') != (str(root) if root else None):
            raise Rejected('IDEMPOTENCY_CONFLICT', 'The original command used a different vendor entry or source')
        unit = approved['candidate_unit']
    if expected_unit is not None and expected_unit != unit:
        raise Rejected('VENDOR_UNIT_MISMATCH', f'Expected {expected_unit!r}, but the live subitem unit is {unit!r}')
    return vendor, unit, lookup_runtime


def prepare_entry(catalog_path, state, inspector_path, project_id, parent_id, quantity, command_id, entry_id, output_root, *, candidate_name=None, candidate_specialty=None, candidate_code=None, candidate_unit=None, parent_sections=None, save_timeout=DEFAULT_SAVE_TIMEOUT, save=True, selected_anchor=False, navigate_anchor=False, vendor_id=None, library_root=None, expect_unit=None, item_label=None):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', command_id):
        raise Rejected('COMMAND_ID_INVALID', 'Use a bounded ASCII business command identifier')
    if parent_sections is not None:
        if (parent_id is not None or not 1 <= len(parent_sections) <= 10
                or any(not isinstance(name, str) or not name.strip() or len(name) > 240
                       or any(ord(ch) < 32 for ch in name) for name in parent_sections)):
            raise Rejected('PARENT_SECTION_INVALID', 'Provide one to ten exact section names instead of a parent ID')
        parent_sections = list(parent_sections)
        section_key = json.dumps(parent_sections, ensure_ascii=False, separators=(',', ':'))
        parent_id = 'section.' + hashlib.sha256(section_key.encode()).hexdigest()
    elif not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', parent_id or ''):
        raise Rejected('PARENT_ID_INVALID', 'An explicit parent business identifier is required')
    if entry_id is not None and (candidate_code is not None or candidate_unit is not None):
        raise Rejected('CATALOG_ARGUMENTS', 'With --entry-id, code and unit come from the catalog')
    explicit = any(value is not None for value in (candidate_name, candidate_specialty, candidate_code, candidate_unit))
    if vendor_id is not None and (vendor_id <= 0 or catalog_path is not None or entry_id is not None or explicit):
        raise Rejected('CATALOG_ARGUMENTS', 'Provide a positive vendor ID without SQLite or explicit candidate fields')
    if vendor_id is None and (library_root is not None or expect_unit is not None):
        raise Rejected('CATALOG_ARGUMENTS', '--library-root and --expect-unit require --vendor-id')
    if entry_id is None and vendor_id is None and not explicit:
        raise Rejected('CANDIDATE_IDENTITY_REQUIRED', 'Provide a vendor ID, SQLite entry ID or all four exact candidate fields')
    request = dict(command_id=command_id, project_id=project_id, entry_id=entry_id,
                   parent_id=parent_id, save=save)
    save_timeout_seconds(save_timeout)
    if save_timeout != DEFAULT_SAVE_TIMEOUT:
        if not save:
            raise Rejected('SAVE_ARGUMENTS', 'A save timeout requires Save to be enabled')
        request['save_timeout'] = save_timeout
    if parent_sections is not None:
        request['parent_sections'] = parent_sections
    if item_label is not None:
        if (not selected_anchor or item_label != item_label.strip()
                or not re.fullmatch(r'[^\x00-\x1f\x7f\[\]"\\]{1,40}', item_label)):
            raise Rejected('ITEM_LABEL_INVALID', 'Provide a distinct one to forty character label for a section-path quota')
        request['item_label'] = item_label
    if selected_anchor:
        require_quota_execution_scope(parent_sections, selected_anchor=True, save=save)
        request['selected_anchor'] = True
    if navigate_anchor:
        if not selected_anchor:
            raise Rejected('SECTION_ANCHOR_ARGUMENTS', '--navigate-anchor requires --selected-anchor')
        request['navigate_anchor'] = True
    quantity = normalize_quantity(quantity)
    directory = Path(output_root).resolve() / 'commands' / hashlib.sha256(command_id.encode()).hexdigest()[:20]
    manifest = directory/'runtime.json'
    approved = runtime_config(manifest)[0]['approved_request'] if manifest.exists() else None
    lookup_runtime = None
    if vendor_id is not None:
        vendor, candidate_unit, lookup_runtime = resolve_vendor_identity(
            state, output_root, project_id, library_root, vendor_id, expect_unit, approved)
        request['vendor_entry'] = vendor
        if expect_unit is not None:
            request['vendor_expected_unit'] = expect_unit
        candidate_code, candidate_name, candidate_specialty = (vendor[key] for key in ('code', 'name', 'specialty'))
        explicit = True
    if explicit:
        request.update(candidate_name=candidate_name, candidate_specialty=candidate_specialty)
    if entry_id is None:
        request.update(candidate_code=candidate_code, candidate_unit=candidate_unit)
    entry = request_catalog_entry(catalog_path, request)
    identity, quantity = candidate_identity(entry, quantity, name=candidate_name, specialty=candidate_specialty)
    display = displayed_quantity(identity['unit'], quantity)
    request['quantity'] = quantity
    if approved is not None:
        if approved != request:
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
        if parent_sections is not None:
            parent = find_section_path(budget['rows'], parent_sections)
        else:
            parents = [r for r in budget['rows'] if r['values'][1] == '部' and r['values'][12] == parent_id]
            if len(parents) != 1 or parents[0]['depth'] != 1:
                raise Rejected('PARENT_NOT_UNIQUE', 'A unique top-level budget parent is required')
            parent = parents[0]
        children = [r for r in budget['rows'] if r['row_path_session_only'].rsplit('/',1)[0] == parent['row_path_session_only']]
        children.sort(key=lambda r:int(r['row_path_session_only'].rsplit('/',1)[1]))
        if not 1 <= len(children) <= 99:
            raise Rejected('PARENT_SHAPE_NOT_REVIEWED', 'Non-empty bounded parent required by this internal worker')
        if parent_sections is not None and children[-1]['values'][1] != '定':
            raise Rejected('PARENT_SHAPE_NOT_REVIEWED', 'Section path requires a quota row as its last direct child')
        anchor = children[-1]
        anchor_check = None
        preceding_anchor_values = None
        navigation_rows = []
        direct_anchor = False
        if parent_sections is not None:
            cursor = budget['current']
            def current_is(row):
                return bool(cursor.get('verified')
                            and cursor.get('row_path_session_only') == row['row_path_session_only']
                            and cursor.get('values') == row['values'])

            if (selected_anchor and len(children) > 1 and is_blank_quota(anchor['values'])
                    and children[-2]['values'][0] and children[-2]['values'][1] == '定'
                    and current_is(children[-2])):
                preceding_anchor_values = children[-2]['values']
            if navigate_anchor and not current_is(anchor) and preceding_anchor_values is None:
                navigation_rows = bounded_anchor_route(budget['rows'], cursor, anchor)
                direct_anchor = cursor.get('verified') is True and not navigation_rows
            anchor_check = dict(path=anchor['row_path_session_only'], code=anchor['values'][0],
                name=anchor['values'][2], selected=current_is(anchor),
                trailing_blank_after_selected_quota=preceding_anchor_values is not None,
                direct_navigation=direct_anchor,
                navigation_paths=[row['row_path_session_only'] for row in navigation_rows])
            if selected_anchor and not (anchor_check['selected'] or preceding_anchor_values is not None
                                        or navigation_rows or direct_anchor):
                raise Rejected('SECTION_ANCHOR_NOT_SELECTED', 'Select the final direct quota in the target section')
            if selected_anchor:
                for row in children:
                    values = row['values']
                    if values[:2] != [identity['code'], '定'] or values[3] != identity['unit']:
                        continue
                    name = re.sub(r' \[CLI:[0-9a-f]{8}\]$', '', values[2])
                    base, _, prior_scope = name.removesuffix(']').partition(' [用途:') if ' [用途:' in name else (name, '', '')
                    if base == identity['name'] and (item_label is None or item_label == prior_scope):
                        raise Rejected('DUPLICATE_SECTION_QUOTA', 'The exact quota already exists for this scope')
        directory.mkdir(parents=True, exist_ok=False)
        generated = render(TEMPLATES, directory, entry=entry, identity=identity,
            quantity=quantity, request={**request, '_state':str(Path(state).resolve())}, session=session,
            profile=profile_path(session['profile_id']),
            title_prefix=project_window_prefix(meta['workcopy_path']), owner=project['owner'],
            budget_count=len(budget['rows']), parent_name=parent['values'][2],
            parent_path=parent['row_path_session_only'], anchor_values=anchor['values'], child_count=len(children),
            preceding_anchor_values=preceding_anchor_values, navigation_rows=navigation_rows,
            direct_anchor=direct_anchor)
        for name, text in generated.items():
            (directory/name).write_text(text, encoding='utf-8', newline='\n')
        (directory/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (directory/'preparation.json').write_text(json.dumps(dict(catalog_entry=entry,capture=capture,
            session=session,owner=project['owner'],business_dispatches=0,anchor_check=anchor_check,
            vendor_lookup_runtime=lookup_runtime),
            ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        store.close()
    compile_bridge(directory)
    worker = directory/'worker.py'
    pins = {str(directory/name):hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in generated}
    binary = directory/'build/gccp_business.dll'
    pins[str(binary)] = hashlib.sha256(binary.read_bytes()).hexdigest()
    config = dict(channel='native_qt_business', live_candidate_check_required=True, worker=str(worker),
        worker_sha256=pins[str(worker)], artifact_sha256=pins, catalog_identity_digest=entry['identity_digest'],
        expected_live_identity=identity, approved_request=request, catalog_match='live_exact_candidate' if explicit else 'live_reviewed_family',
        scope='Beijing2025, exact live candidate, positive quantity, current pinned process')
    if selected_anchor:
        config['selected_anchor_contract'] = 1
    if display != quantity:
        config['expected_display_quantity'] = display
    manifest.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest, directory/'request.json', request
