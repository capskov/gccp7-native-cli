"""Plan reuse of an existing selected quota from a read-only native capture."""

from .native_cli import Rejected
from .native_runtime import find_section_path, normalize_quantity
from .native_worker_support import rows, snap, table


def plan_reuse(capture, request):
    if capture.get('schema') == 'gccp.native-cli.v0.1':
        if capture.get('status') != 'ok':
            raise Rejected('REUSE_CAPTURE_INVALID', 'Use a successful native-inspect capture')
        capture = capture['data']
    budget = table(capture, '工程量表达式')
    resources = table(capture, '单位')
    if (budget['headers'][:7] != ['编码', '类别', '名称', '单位', '工程量表达式', '含量', '工程量']
            or resources['headers'][:5] != ['编码', '类别', '名称', '规格及型号', '单位']
            or not budget['rows'] or budget['rows'][0]['values'][2] != '整个项目'):
        raise Rejected('REUSE_CAPTURE_INVALID', 'Use the entire project budget and its selected quota resource table')

    selected = budget['current']
    source_path = selected.get('row_path_session_only')
    source = rows(budget).get(source_path)
    quota = request['quota']
    identity = [quota['code'], '定', quota['name'], quota['unit']]
    if (not selected.get('verified') or source is None or source['values'] != selected['values']
            or source['values'][:4] != identity):
        raise Rejected('REUSE_SOURCE_MISMATCH', 'Select the exact reviewed ordinary quota before capturing')

    material_code = request['source_material_code']
    materials = [row['values'] for row in resources['rows']
                 if row['values'][:2] == [material_code, '主']]
    if len(materials) != 1:
        raise Rejected('REUSE_MATERIAL_NOT_UNIQUE', 'The source material code must identify one main material')
    material = materials[0]
    children = [row for row in budget['rows']
                if row['row_path_session_only'].rsplit('/', 1)[0] == source_path
                and row['values'][:4] == [material[0], '主', material[2], material[4]]]
    if len(children) != 1:
        raise Rejected('REUSE_MATERIAL_OWNER_UNVERIFIED', 'The material must match a direct child of the selected quota')

    variants = request['variants']
    if not isinstance(variants, list) or not 1 <= len(variants) <= 100:
        raise Rejected('REUSE_VARIANTS_INVALID', 'Provide 1 to 100 reviewed material variants')
    seen = set()
    plan = []
    for variant in variants:
        section = variant['parent_sections']
        if not isinstance(section, list) or not section or not all(isinstance(n, str) and n.strip() for n in section):
            raise Rejected('REUSE_SECTION_INVALID', 'Provide exact section names from the budget root')
        parent = find_section_path(budget['rows'], section)['row_path_session_only']
        target_material = [variant[key] for key in ('material_code', 'material_name', 'material_spec', 'material_unit')]
        if (not all(isinstance(value, str) for value in target_material)
                or not all(target_material[index].strip() for index in (0, 1, 3))):
            raise Rejected('REUSE_VARIANT_INVALID', 'Provide exact material code, name, specification and unit')
        key = (parent, *target_material)
        if key in seen:
            raise Rejected('REUSE_VARIANT_DUPLICATE', 'Combine repeated quantities for the same section and material first')
        seen.add(key)
        quantity = normalize_quantity(variant['quantity'])
        candidates = [dict(path=row['row_path_session_only'], values=row['values'])
                      for row in budget['rows']
                      if row['row_path_session_only'].rsplit('/', 1)[0] == parent
                      and (row['values'][0] == identity[0] or row['values'][0].startswith(identity[0] + ' '))
                      and row['values'][1] in ('定', '换')]
        same_source_material = target_material == [material[0], *material[2:5]]
        if same_source_material and len(candidates) == 1 and candidates[0]['path'] == source_path:
            decision = ('keep_source' if source['values'][6] == quantity
                        else 'review_source_quantity')
        elif any(candidate['path'] != source_path for candidate in candidates):
            decision = 'review_existing_rows'
        else:
            decision = 'copy_candidate'
        plan.append(dict(parent_sections=section, material=target_material, quantity=quantity,
                         decision=decision, existing_quota_candidates=candidates))

    return dict(status='quota_reuse_plan_only', business_dispatches=0, budget_digest=snap(budget).sha256,
                source_path=source_path, source_quota=source['values'], source_material=material,
                variants=plan)
