"""Initialize and check land-audit records, never certify business facts."""
import argparse
from collections import Counter
import copy
import csv
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'catalog/check_catalog.csv'
SCHEMA = ROOT / 'contracts/land_audit.schema.json'
DONE = {'PASS', 'FINDING', 'NA'}


def load_catalog():
    with CATALOG.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def _selected(obj, mode, stage):
    modules = set(obj['modules']) | {'common'}
    if stage in {'PAYMENT', 'SETTLEMENT'}:
        modules.add('payment')
    return [r for r in load_catalog()
            if r['module'] in modules and mode in r['modes'].split(';')]


def new_ledger(objects, project_id, mode='FULL', stage='INITIAL'):
    objects = copy.deepcopy(objects)
    checks = []
    for obj in objects:
        obj.setdefault('source_refs', [])
        for entry in _selected(obj, mode, stage):
            checks.append(dict(object_id=obj['object_id'], check_id=entry['check_id'],
                status='NOT_STARTED', observation='', source_refs=[], evidence_refs=[],
                calculation_ref='', policy_ids=[], reason='', next_action='',
                reviewed_by='', review_round='', issue_ids=[]))
    return dict(schema_version='1.0', meta=dict(project_id=project_id, mode=mode,
        stage=stage, scope_basis='', rounding_basis=''), sources=[], policies=[],
        objects=objects, checks=checks, fees=[], issues=[])


def _money(value):
    return Decimal(value) if value is not None else None


def check_ledger(data, verify_sources=False, base_dir=None):
    """Validate record consistency. All substantive evidence remains unverified."""
    report = dict(ok=False, errors=[], warnings=[], review_readiness='IN_PROGRESS',
                  business_verified=False, source_hash_check='NOT_CHECKED',
                  coverage={}, amounts={})
    errors = report['errors']
    schema = json.loads(SCHEMA.read_text(encoding='utf-8'))
    for error in Draft202012Validator(schema).iter_errors(data):
        errors.append(f"{'/'.join(map(str, error.absolute_path))}: {error.message}")
    if errors:
        return report

    def require(condition, message):
        if not condition:
            errors.append(message)

    def index(rows, key):
        result = {}
        for row in rows:
            require(row[key] not in result, f'重复{key}: {row[key]}')
            result[row[key]] = row
        return result

    sources = index(data['sources'], 'source_id')
    policies = index(data['policies'], 'policy_id')
    objects = index(data['objects'], 'object_id')
    fees = index(data['fees'], 'fee_id')
    issues = index(data['issues'], 'issue_id')
    meta = data['meta']
    expected = {(o['object_id'], r['check_id']): r for o in data['objects']
                for r in _selected(o, meta['mode'], meta['stage'])}
    actual = Counter((c['object_id'], c['check_id']) for c in data['checks'])
    for key in expected:
        require(actual[key] == 1, f'检查项应恰有一条: {key}, 实有{actual[key]}')
    for key in actual:
        require(key in expected, f'检查项超出对象模块/模式: {key}')

    for collection in ['objects', 'policies', 'checks', 'fees', 'issues']:
        for row in data[collection]:
            for ref in row['source_refs']:
                require(ref['source_id'] in sources, f'{collection}: 未登记来源{ref["source_id"]}')
            if collection in {'checks', 'fees', 'issues'}:
                require(row['object_id'] in objects, f'{collection}: 未登记对象{row["object_id"]}')
            for pid in row.get('policy_ids', []):
                require(pid in policies, f'{collection}: 未登记政策{pid}')
            for iid in row.get('issue_ids', []):
                require(iid in issues, f'{collection}: 未登记问题{iid}')
                if iid in issues:
                    require(issues[iid]['object_id'] == row['object_id'], f'跨对象问题引用{iid}')
    for obj in objects.values():
        if obj.get('parent_id'):
            seen = {obj['object_id']}
            parent = obj['parent_id']
            while parent:
                require(parent in objects, f'未登记父对象{parent}')
                require(parent not in seen, f'对象父子关系成环{parent}')
                if parent not in objects or parent in seen:
                    break
                seen.add(parent)
                parent = objects[parent].get('parent_id', '')
    for policy in policies.values():
        if policy['status'] == 'APPLICABLE':
            for field in ['region_basis', 'time_basis', 'subject_basis', 'adoption_basis', 'source_refs']:
                require(bool(policy[field]) and (not isinstance(policy[field], str) or bool(policy[field].strip())),
                        f'{policy["policy_id"]}: 适用政策缺{field}')
    for row in data['checks']:
        key = (row['object_id'], row['check_id'])
        label = '/'.join(key)
        state = row['status']
        for iid in row['issue_ids']:
            if iid in issues:
                require(row['check_id'] in issues[iid]['check_ids'], f'{label}: 问题未反向关联{iid}')
        if state in DONE:
            require(bool(row['source_refs']), f'{label}: 已处理检查缺来源')
            for field in ['reviewed_by', 'review_round']:
                require(bool(row[field].strip()), f'{label}: 缺{field}')
        if state in {'PASS', 'FINDING'}:
            require(bool(row['observation'].strip()), f'{label}: 缺实际观察')
            spec = expected.get(key, {})
            if spec.get('requires_calc') == '1':
                require(bool(row['calculation_ref'].strip()), f'{label}: 缺独立计算引用')
            if spec.get('requires_policy') == '1':
                require(bool(row['policy_ids']), f'{label}: 缺政策引用')
                if state == 'PASS':
                    require(all(policies.get(p, {}).get('status') == 'APPLICABLE' for p in row['policy_ids']),
                            f'{label}: 政策未适用不能PASS')
        if state == 'FINDING':
            require(bool(row['issue_ids']), f'{label}: FINDING未关联问题')
        if state in {'NA', 'BLOCKED'}:
            require(bool(row['reason'].strip()), f'{label}: 缺原因')
        if state == 'BLOCKED':
            require(bool(row['next_action'].strip()), f'{label}: 缺下一步')
    for issue in issues.values():
        iid = issue['issue_id']
        require(bool(issue['source_refs']), f'{iid}: 缺问题来源')
        require(bool(issue['check_ids']), f'{iid}: 缺检查项关系')
        for cid in issue['check_ids']:
            require((issue['object_id'], cid) in expected, f'{iid}: 未登记检查项{cid}')
            linked_checks = [c for c in data['checks'] if c['object_id']==issue['object_id'] and c['check_id']==cid]
            require(any(iid in c['issue_ids'] for c in linked_checks), f'{iid}: 检查项未反向关联问题{cid}')
        if issue['money_effect'] != 'NONE':
            require(bool(issue['fee_ids']), f'{iid}: 金额问题未关联费用')
        for fid in issue['fee_ids']:
            require(fid in fees, f'{iid}: 未登记费用{fid}')
            if fid in fees:
                require(fees[fid]['object_id'] == issue['object_id'], f'{iid}: 跨对象费用引用')
                require(iid in fees[fid]['issue_ids'], f'{iid}: 费用未反向关联问题')
        if issue['status'] in {'CLOSED', 'WITHDRAWN'}:
            require(bool(issue['review_ref'].strip()) and bool(issue['close_reason'].strip()), f'{iid}: 缺关闭复核或理由')
    # Increase precision for exact aggregation of any accepted ordinary decimal string.
    precision = max([28] + [len(v) + 10 for f in fees.values()
                           for k, v in f.items() if k.endswith('_amount') and v is not None])
    with localcontext() as ctx:
        ctx.prec = precision + len(str(len(fees) + 1))
        for fee in fees.values():
            fid = fee['fee_id']
            require(bool(fee['source_refs']), f'{fid}: 缺费用来源')
            require(bool(fee['decision_basis'].strip()), f'{fid}: 缺处置依据')
            for field in ['submitted_amount', 'calculated_amount', 'adopted_amount', 'pending_amount']:
                require(fee[field] is None or _money(fee[field]) >= 0, f'{fid}: {field}不能为负')
            for iid in fee['issue_ids']:
                if iid in issues:
                    require(fid in issues[iid]['fee_ids'], f'{fid}: 问题未反向关联费用')
            if fee['decision'] in {'PENDING', 'OUT_OF_SCOPE'}:
                require(fee['adopted_amount'] is None and fee['adjustment_amount'] is None,
                        f'{fid}: 待核/范围外不能填采用或调整金额')
                if fee['decision'] == 'OUT_OF_SCOPE':
                    require(_money(fee['pending_amount']) == 0, f'{fid}: 范围外待核额应为0')
            else:
                require(meta['mode'] == 'FULL', f'{fid}: 专项模式不得确认采用金额')
                for field in ['scope_basis', 'rounding_basis']:
                    require(bool(meta[field].strip()), f'{fid}: 缺项目{field}')
                require(bool(objects.get(fee['object_id'], {}).get('source_refs')), f'{fid}: 对象缺来源关系')
                required = ['submitted_amount', 'calculated_amount', 'adopted_amount', 'adjustment_amount']
                require(all(fee[k] is not None for k in required), f'{fid}: 确认金额不完整')
                if all(fee[k] is not None for k in required):
                    require(_money(fee['adopted_amount']) == _money(fee['submitted_amount']) + _money(fee['adjustment_amount']),
                            f'{fid}: 采用额不等于送审额加调整额')
                require(_money(fee['pending_amount']) == 0, f'{fid}: 已确认仍有待核金额')
                require(bool(fee['calculation_ref'].strip()) and bool(fee['review_ref'].strip()), f'{fid}: 缺计算或复核记录')
                require(bool(fee['policy_ids']) and all(policies.get(p, {}).get('status') == 'APPLICABLE' for p in fee['policy_ids']),
                        f'{fid}: 政策尚未确认适用')
                obj_checks = [c for c in data['checks'] if c['object_id'] == fee['object_id']]
                require(bool(obj_checks) and all(c['status'] in DONE for c in obj_checks), f'{fid}: 对象仍有未完成检查')
                relevant = [i for i in issues.values() if fid in i['fee_ids'] and i['money_effect'] != 'NONE']
                require(all(i['status'] in {'CLOSED', 'WITHDRAWN'} for i in relevant), f'{fid}: 金额问题未闭环')
        inside = [f for f in fees.values() if f['decision'] != 'OUT_OF_SCOPE']
        confirmed = [f for f in inside if f['decision'] == 'CONFIRMED']
        pending = [f for f in inside if f['decision'] == 'PENDING']
        def total(rows, field):
            return format(sum((_money(f[field]) for f in rows if f[field] is not None), Decimal(0)), '.2f')
        unknown = sum(f['submitted_amount'] is None for f in inside)
        report['amounts'] = dict(submitted_total=total(inside, 'submitted_amount') if inside and not unknown else None,
            unknown_submitted_count=unknown, confirmed_adopted_subtotal=total(confirmed, 'adopted_amount'),
            confirmed_adjustment=total(confirmed, 'adjustment_amount'), pending_known=total(pending, 'pending_amount'),
            pending_unknown_count=sum(f['pending_amount'] is None for f in pending),
            adopted_total=total(confirmed, 'adopted_amount') if inside and not pending and not unknown else None,
            out_of_scope_known_submitted=total([f for f in fees.values() if f['decision']=='OUT_OF_SCOPE'], 'submitted_amount'))
    if verify_sources:
        base = Path(base_dir or Path.cwd())
        hash_ok = True
        for source in sources.values():
            path = Path(source['path'])
            if not path.is_absolute():
                path = base / path
            try:
                hasher = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                if digest != source['sha256']:
                    hash_ok = False
                    errors.append(f'{source["source_id"]}: 来源SHA256不符')
            except OSError as exc:
                hash_ok = False
                errors.append(f'{source["source_id"]}: 来源读取失败: {exc}')
        report['source_hash_check'] = ('MATCHED' if hash_ok else 'FAILED') if sources else 'NO_SOURCES'
    counts = Counter(c['status'] for c in data['checks'])
    mapped_objects = {f['object_id'] for f in fees.values()}
    for oid in list(mapped_objects):
        seen = {oid}
        parent = objects.get(oid, {}).get('parent_id', '')
        while parent in objects and parent not in seen:
            mapped_objects.add(parent)
            seen.add(parent)
            parent = objects[parent].get('parent_id', '')
    unmapped = sorted(set(objects) - mapped_objects)
    def coverage_group(rows, expected_count):
        grouped = Counter(c['status'] for c in rows)
        return dict(expected=expected_count, statuses=dict(grouped),
                    processed=sum(grouped[s] for s in DONE),
                    substantive=grouped['PASS'] + grouped['FINDING'])
    report['coverage'] = dict(expected_checks=len(expected), actual_checks=len(data['checks']),
        statuses=dict(counts), processed=sum(counts[s] for s in DONE),
        substantive=counts['PASS'] + counts['FINDING'], objects=len(objects),
        fees=len(fees), objects_without_fees=unmapped,
        by_object={oid: coverage_group([c for c in data['checks'] if c['object_id']==oid],
                   sum(k[0]==oid for k in expected)) for oid in objects},
        by_module={module: coverage_group([c for c in data['checks']
                   if expected.get((c['object_id'], c['check_id']), {}).get('module')==module],
                   sum(r['module']==module for r in expected.values()))
                   for module in sorted({r['module'] for r in expected.values()})},
        scope_basis_recorded=bool(meta['scope_basis'].strip()),
        denominator_note='仅台账范围；须与原始文件/页/行枚举清单另行核对')
    complete = (bool(expected) and all(c['status'] in DONE for c in data['checks'])
                and all(i['status'] in {'CLOSED','WITHDRAWN'} for i in issues.values())
                and bool(meta['scope_basis'].strip())
                and all(o['source_refs'] for o in objects.values()))
    if meta['mode'] == 'FULL':
        complete = complete and bool(fees) and not pending and not unmapped and bool(meta['rounding_basis'].strip())
    elif meta['mode'] == 'ARITHMETIC':
        complete = complete and bool(meta['rounding_basis'].strip())
    report['ok'] = not errors
    report['review_readiness_scope'] = meta['mode']
    if complete and not errors:
        report['review_readiness'] = 'COMPLETE_RECORDS'
    report['warnings'].append('仅校验记录约束；来源内容、证据真实性、政策适用判断和实际文件显示须另行核验。')
    if errors or not complete or meta['mode'] != 'FULL':
        report['amounts']['adopted_total'] = None
    return report


def _write_json(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def _same_file(left, right):
    left, right = Path(left), Path(right)
    return left.resolve() == right.resolve() or (left.exists() and right.exists() and left.samefile(right))


def main(argv=None, renderer=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='完整输出；默认超限保存文件')
    sub = parser.add_subparsers(dest='command', required=True)
    catalog = sub.add_parser('catalog')
    catalog.add_argument('--module')
    catalog.add_argument('--mode', choices=['FULL','DOCUMENT','ARITHMETIC','POLICY'], default='FULL')
    catalog.add_argument('--stage', choices=['INITIAL','PAYMENT','SETTLEMENT'], default='INITIAL')
    catalog.add_argument('--check-id', help='只读一项检查的完整动作与要求')
    init = sub.add_parser('init')
    init.add_argument('--objects', required=True)
    init.add_argument('--project', required=True)
    init.add_argument('--mode', choices=['FULL','DOCUMENT','ARITHMETIC','POLICY'], default='FULL')
    init.add_argument('--stage', choices=['INITIAL','PAYMENT','SETTLEMENT'], default='INITIAL')
    init.add_argument('--output', required=True)
    check = sub.add_parser('check')
    check.add_argument('ledger')
    check.add_argument('--verify-sources', action='store_true')
    check.add_argument('--output')
    for command_parser in (catalog, init, check):
        command_parser.add_argument('--full', action='store_true', default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    render_result = renderer or (lambda value, full=False: json.dumps(value, ensure_ascii=False, indent=2))
    try:
        if args.command == 'catalog':
            modules = [args.module] if args.module else sorted({r['module'] for r in load_catalog()} - {'payment'})
            rows = _selected({'modules': modules}, args.mode, args.stage)
            if args.check_id:
                rows = [r for r in rows if r['check_id'] == args.check_id]
            if not rows:
                raise ValueError('没有匹配的模块/模式检查项')
            print(render_result(rows, full=args.full))
            return 0
        if args.command == 'init':
            with Path(args.objects).open(encoding='utf-8-sig', newline='') as stream:
                objects = list(csv.DictReader(stream))
            for obj in objects:
                obj['modules'] = [v.strip() for v in obj.get('modules','').split(';') if v.strip()]
            data = new_ledger(objects, args.project, args.mode, args.stage)
            result = check_ledger(data)
            if not result['ok']:
                raise ValueError('; '.join(result['errors']))
            _write_json(args.output, data, exclusive=True)
            print(f'已初始化 {len(objects)} 个对象、{len(data["checks"])} 项待审检查：{args.output}')
            return 0
        source = Path(args.ledger)
        if args.output and _same_file(args.output, source):
            raise ValueError('校验报告不能覆盖输入台账')
        data = json.loads(source.read_text(encoding='utf-8-sig'))
        result = check_ledger(data, args.verify_sources, source.parent)
        if args.output:
            output = Path(args.output).resolve()
            for s in data.get('sources', []) if isinstance(data, dict) and isinstance(data.get('sources'), list) else []:
                if not isinstance(s, dict) or not isinstance(s.get('path'), str):
                    continue
                target = Path(s.get('path',''))
                target = target if target.is_absolute() else source.parent / target
                if _same_file(target, output):
                    raise ValueError('校验报告不能覆盖登记来源')
            _write_json(args.output, result)
        print(render_result(result, full=args.full))
        return 0 if result['ok'] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'错误: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
