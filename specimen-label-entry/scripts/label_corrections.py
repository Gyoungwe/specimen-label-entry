#!/usr/bin/env python3
"""Reconcile damaged label fields against independently reviewed, clear labels.

Image inspection and quality annotation belong to the host's vision workflow.
This module checks shared-event evidence, preserves observations, and reports
each borrowed value. Repaired fields never become reference observations.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
import json
import math
from pathlib import Path
import unicodedata
from label_intervals import observations as interval_observations

QUALITIES = {'clear', 'unclear', 'damaged', 'missing'}
QUALITY_LABELS = {'clear': '清晰', 'unclear': '模糊', 'damaged': '破损', 'missing': '缺失'}
SHARED_FIELDS = frozenset(['国家', '省', '地级市', '区县', '具体地点', '经度', '纬度', '海拔', '采集日期', '采集人'])
ROLE_FIELDS = {'catalogue': {'标本号'}, 'collection': SHARED_FIELDS | {'采集方式'}, 'scale': set(),
               'identification': {'中文名称', '门', 'phylum', '纲', 'class', '目', 'order', '中文科名', '科名',
                                  '属名', '种名', '亚种名', '作者', '年代', '鉴定人', '鉴定日期'}}
ANCHORS = [('具体地点', '采集日期'), ('经度', '纬度', '采集日期')]
STATUS_LABELS = {'applied': '跨标签补全，待核对', 'pending': '证据不足，留空待核对',
                 'ambiguous': '参考标签冲突，留空待核对', 'rule_managed': '按规则或用户确认处理，待核对',
                 'non_scalar': '标签为区间，单值留空待核对'}
REVIEW_HEADERS = ['来源文件', '来源页/图', '输出分册', 'Excel行', '标本号', '字段', '标签状况',
                  '原始读数', '填写值', '处理结果', '参考来源', '匹配依据', '需核对事项', '人工核对结果', '人工备注']
REVIEW_WIDTHS = [32, 12, 11, 10, 23, 16, 12, 30, 30, 32, 85, 65, 70, 18, 45]


def display(value):
    return '留空' if value is None else str(value)


def valid_value(field, value):
    if value is None or isinstance(value, bool):
        return False
    if field in ('经度', '纬度', '海拔'):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
        limit = 180 if field == '经度' else 90 if field == '纬度' else None
        return limit is None or abs(value) <= limit
    if field == '采集日期':
        if not isinstance(value, str):
            return False
        try:
            return date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False
    return isinstance(value, str) and bool(value.strip())


def normalized(value):
    if isinstance(value, str):
        return ' '.join(unicodedata.normalize('NFKC', value).split())
    return value


def clear(record, field):
    return field not in record.get('interval_observations',{}) and record.get('field_quality', {}).get(field) == 'clear' and valid_value(field, record['observed_values'].get(field))


def reviewed_reference(record):
    raw = record.get('collection_label_text')
    return record.get('visually_checked') is True and isinstance(raw, str) and bool(raw.strip())


def signatures(record):
    return [(fields, tuple(normalized(record['observed_values'][field]) for field in fields))
            for fields in ANCHORS if all(clear(record, field) for field in fields)]


def known_conflicts(target, donor):
    return [field for field in SHARED_FIELDS if clear(target, field) and clear(donor, field)
            and normalized(target['observed_values'][field]) != normalized(donor['observed_values'][field])]


def prepare_records(batch, headers=None):
    records = batch.get('records', [])
    seen = set()
    for index, record in enumerate(records, 1):
        values = record.get('values', {})
        if not isinstance(values, dict):
            raise ValueError('Record values must be an object')
        key = record.get('record_id') or f'{record.get("source")}/{record.get("page")}/{record.get("group_id") or values.get("标本号") or index}'
        if not isinstance(key, str) or not key.strip() or key in seen:
            raise ValueError('Duplicate or invalid correction record_id')
        seen.add(key)
        record['record_id'] = key
        quality = record.get('field_quality', {})
        labels = record.get('label_quality', {})
        for mapping in [quality, labels]:
            if not isinstance(mapping, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or v not in QUALITIES for k, v in mapping.items()):
                raise ValueError('Quality annotations must map field/label names to clear, unclear, damaged or missing')
        if headers is not None and set(quality) - set(headers):
            raise ValueError(f'Unknown quality fields: {set(quality) - set(headers)}')
        if set(labels) - {'catalogue', 'collection', 'identification', 'scale'}:
            raise ValueError('Unknown label role in label_quality')
        quality = dict(quality)
        for field in record.get('interval_observations',{}):
            quality.setdefault(field,'clear')
        for role, state in labels.items():
            for field in sorted(ROLE_FIELDS[role]):
                if field in values and field not in quality:
                    quality[field] = state
        record['field_quality'] = quality
        observed = record.get('observed_values', values)
        if not isinstance(observed, dict):
            raise ValueError('observed_values must be an object')
        observed = deepcopy(observed)
        for field in quality:
            if field not in observed:
                observed[field] = values.get(field)
        record['observed_values'] = observed
        record['values'] = deepcopy(values)
        record['label_corrections'] = []
        for field, state in quality.items():
            if state != 'clear' or field in record.get('interval_observations',{}):
                record['values'][field] = None
        if record.get('source') not in batch.get('sources', {}):
            raise ValueError('Correction record has an unknown source')
        page = record.get('page')
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= batch['sources'][record['source']]['page_count']:
            raise ValueError('Correction record has an invalid source page')
    return records


def confirmed_groups(batch, by_id):
    memberships = defaultdict(list)
    seen = set()
    for group in batch.get('shared_collection_groups', []):
        key = group.get('id')
        evidence = group.get('evidence', {})
        fields = group.get('fields', sorted(SHARED_FIELDS))
        members = group.get('record_ids', [])
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('Shared collection groups require unique IDs')
        seen.add(key)
        if evidence.get('type') != 'user_confirmation' or not isinstance(evidence.get('statement'), str) or not evidence['statement'].strip():
            raise ValueError('A shared collection group requires an actual user confirmation statement')
        if not isinstance(fields, list) or not fields or any(not isinstance(f, str) for f in fields) or set(fields) - SHARED_FIELDS:
            raise ValueError('Only shared collection fields can be confirmed as a group')
        if not isinstance(members, list) or len(members) < 2 or any(not isinstance(m, str) or m not in by_id for m in members) or len(set(members)) != len(members):
            raise ValueError('Shared group record_ids must name distinct existing records')
        for member in members:
            memberships[member].append({'id': key, 'fields': fields, 'record_ids': members, 'evidence': evidence})
    return memberships


def source_reference(batch, donor, field, basis):
    source = batch['sources'][donor['source']]
    return {'record_id': donor['record_id'], 'source': donor['source'], 'source_name': source.get('name', donor['source']),
            'page': donor['page'], 'group_id': donor.get('group_id'), 'specimen': donor['observed_values'].get('标本号'),
            'field': field, 'value': donor['observed_values'][field],
            'source_sha256': source.get('sha256') or (donor.get('image_provenance') or {}).get('source_sha256'), 'basis': basis}


def collection_text_acceptable(record):
    raw = record.get('collection_label_text')
    return isinstance(raw, str) and bool(raw.strip()) or (
        raw in (None, '') and record.get('label_quality', {}).get('collection') in {'unclear', 'damaged', 'missing'})


def repair_batch(batch, rules=None, headers=None):
    """Return a repaired copy and report; never mutate the input or raw text."""
    result = deepcopy(batch)
    for record in result.get('records',[]):
        intervals=interval_observations(record)
        if intervals:record['interval_observations']=intervals
    active = any(r.get('field_quality') or r.get('label_quality') or r.get('interval_observations') for r in result.get('records', [])) or bool(result.get('shared_collection_groups'))
    summary = {'enabled': active, 'applied': 0, 'pending': 0, 'ambiguous': 0, 'rule_managed': 0, 'records_affected': 0}
    if not active:
        return result, {'summary': summary, 'items': []}
    records = prepare_records(result, headers)
    by_id = {r['record_id']: r for r in records}
    groups = confirmed_groups(result, by_id)
    index = defaultdict(list)
    for record in records:
        if reviewed_reference(record):
            for key in signatures(record):
                index[key].append(record['record_id'])
    rule_fields = set((rules or {}).get('fixed', {})) | set((rules or {}).get('leave_blank', []))
    rule_fields.update(['采集号', '省代码', '网格号', '采集方式'])
    report = []
    for record in records:
        potential = {}
        for key in signatures(record):
            for reference_id in index[key]:
                if reference_id != record['record_id']:
                    potential.setdefault(reference_id, []).append({'type': 'matching_clear_fields', 'fields': list(key[0])})
        for group in groups.get(record['record_id'], []):
            for reference_id in group['record_ids']:
                if reference_id != record['record_id']:
                    potential.setdefault(reference_id, []).append({'type': 'user_confirmed_group', 'id': group['id'],
                                                                  'fields': group['fields'], 'statement': group['evidence']['statement']})
        for field, quality in record.get('field_quality', {}).items():
            interval=record.get('interval_observations',{}).get(field)
            if quality == 'clear' and not interval:
                continue
            item = {'record_id': record['record_id'], 'source': record['source'], 'page': record['page'],
                    'group_id': record.get('group_id'), 'specimen': record['observed_values'].get('标本号'),
                    'field': field, 'quality': quality, 'original_value': deepcopy(record['observed_values'].get(field)),
                    'value': None, 'status': 'pending', 'reason_code': 'no_matching_reference',
                    'reason': '没有通过共享采集事件核验的清晰参考字段', 'candidates': [], 'donors': [], 'donor_count': 0}
            if interval:
                item.update(status='non_scalar',reason_code='interval_not_scalar',original_value=interval['raw_text'],
                            observed_candidate=deepcopy(record['observed_values'].get(field)),
                            reason='标签原文为区间，单值列留空；不取端点、均值或借用其他标签的单值')
            elif field in rule_fields:
                item.update(status='rule_managed', reason_code='batch_rule', reason='按本批固定或派生规则处理；原标签不清晰仍需核对')
            elif field in ROLE_FIELDS['identification'] and (record.get('identification_evidence') or {}).get('type') == 'user_confirmation' and str((record.get('identification_evidence') or {}).get('statement') or '').strip() and item['original_value'] is not None:
                item.update(status='rule_managed', reason_code='user_identification_confirmation', value=item['original_value'],
                            reason='保留已有用户明确确认的鉴定填写值；来源为用户确认，不是从其他个体标签借用')
                record['values'][field] = item['original_value']
            elif field not in SHARED_FIELDS:
                item.update(reason_code='individual_field', reason='此字段属于独立标本，不能从其他个体标签套用')
            else:
                candidates = {}
                for reference_id, bases in potential.items():
                    donor = by_id[reference_id]
                    basis = [b for b in bases if b['type'] != 'user_confirmed_group' or field in b['fields']]
                    if not reviewed_reference(donor) or not basis or not clear(donor, field) or known_conflicts(record, donor):
                        continue
                    value = donor['observed_values'][field]
                    key = normalized(value)
                    candidate = candidates.setdefault(key, {'value': value, 'donors': [], 'donor_count': 0})
                    candidate['donor_count'] += 1
                    if len(candidate['donors']) < 8:
                        candidate['donors'].append(source_reference(result, donor, field, basis))
                item['candidates'] = list(candidates.values())
                if len(candidates) > 1:
                    item.update(status='ambiguous', reason_code='conflicting_references', reason='匹配到的清晰参考值不一致；不按多数票选择')
                elif candidates:
                    candidate = next(iter(candidates.values()))
                    item.update(donors=candidate['donors'], donor_count=candidate['donor_count'])
                    if record.get('visually_checked') is not True or record.get('cross_label_reviewed') is not True:
                        item.update(reason_code='visual_comparison_pending', reason='已有候选；需实际查看目标与参考标签并核对对应关系')
                    else:
                        item.update(status='applied', reason_code='clear_reference_agreement', value=candidate['value'],
                                    reason='独立清晰参考值一致，已跨标签补全；保留来源并待人工核对')
                        record['values'][field] = candidate['value']
            record['label_corrections'].append(item)
            report.append(deepcopy(item))
        for role, quality in record.get('label_quality', {}).items():
            if quality == 'clear' or any(item['field'] in ROLE_FIELDS[role] for item in record['label_corrections']):
                continue
            item = {'record_id': record['record_id'], 'source': record['source'], 'page': record['page'],
                    'group_id': record.get('group_id'), 'specimen': record['observed_values'].get('标本号'),
                    'field': None, 'label_role': role, 'quality': quality, 'original_value': None, 'value': None,
                    'status': 'pending', 'reason_code': 'field_annotation_needed', 'reason': '标签有质量问题；需先逐字段判读再补全',
                    'candidates': [], 'donors': [], 'donor_count': 0}
            record['label_corrections'].append(item)
            report.append(deepcopy(item))
    summary.update(Counter(item['status'] for item in report))
    summary['records_affected'] = len({item['record_id'] for item in report})
    result['label_correction_summary'] = summary
    return result, {'summary': summary, 'items': report}


def correction_message(item):
    field = item['field'] or item.get('label_role', '标签')
    if item['status']=='non_scalar':
        return f'标签纠错：{field}原文“{item["original_value"]}”为区间；单值留空，不取端点或均值'
    if item['status'] == 'applied':
        donors = '；'.join(f'{d["source_name"]} 第{d["page"]}页/图 {d.get("group_id") or d.get("specimen") or d["record_id"]}' for d in item['donors'])
        return f'标签纠错：{field}原读数“{display(item["original_value"])}”→“{display(item["value"])}”；参考{donors}；待人工核对'
    return f'标签纠错：{field}{QUALITY_LABELS[item["quality"]]}；{item["reason"]}'


def review_table(audit, sources):
    rows = []
    for record in audit:
        for item in record.get('label_corrections', []):
            references, bases = [], []
            for candidate in item.get('candidates', []):
                for donor in candidate['donors']:
                    references.append(f'{donor["source_name"]} 第{donor["page"]}页/图 {donor.get("group_id") or donor.get("specimen") or donor["record_id"]}：{item["field"]}={display(candidate["value"])}')
                    for basis in donor['basis']:
                        bases.append('用户确认组'+basis['id']+'：'+basis['statement'] if basis['type'] == 'user_confirmed_group' else '清晰字段一致：'+'、'.join(basis['fields']))
            if item.get('donor_count', 0) > 8:
                references.append(f'共有{item["donor_count"]}条清晰参考，表中展示前8条；完整原始记录保存在批次 JSON')
            rows.append([sources[record['source']]['name'], record['page'], record['part'], record['excel_row'], record['specimen'],
                         item['field'] or item.get('label_role'), QUALITY_LABELS[item['quality']], display(item['original_value']),
                         display(record['values'].get(item['field'])) if item['field'] else '留空', STATUS_LABELS[item['status']],
                         '；'.join(references), '；'.join(dict.fromkeys(bases)), item['reason'], None, None])
    return {'headers': REVIEW_HEADERS, 'widths': REVIEW_WIDTHS, 'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('records', type=Path)
    parser.add_argument('--rules', type=Path, default=Path(__file__).resolve().parents[1] / 'assets/rules.json')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    paths = [args.out.resolve(), args.report.resolve()]
    if len(set(paths)) != 2 or args.records.resolve() in paths or args.rules.resolve() in paths or any(p.exists() for p in paths):
        raise ValueError('Choose distinct, new outputs and retain input files')
    batch, report = repair_batch(json.loads(args.records.read_text(encoding='utf-8')), json.loads(args.rules.read_text(encoding='utf-8')))
    for path, content in [(args.out, batch), (args.report, report)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps(report['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
