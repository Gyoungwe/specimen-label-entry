#!/usr/bin/env python3
"""Portable workbook backend: fill reference copies and produce the review book.

Usage matches build_workbook.mjs. Main workbooks are edited as OOXML packages;
openpyxl creates only the separate review workbook and reads saved previews.
"""
import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import shutil
from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo
from specimen_data import read_xlsx
from xlsx_copy import digest, fill_copy

YELLOW = PatternFill('solid', fgColor='FFF2CC')
PINK = PatternFill('solid', fgColor='FCE8E6')


def part_path(base, index, count):
    return base if count == 1 else base.with_name(f'{base.stem}_{index:02d}{base.suffix}')


def literal(cell, value):
    cell.value = value
    if isinstance(value, str):
        cell.data_type = 's'


def setup(sheet, title, headers, widths, rows, table_name):
    sheet.sheet_view.showGridLines = False
    sheet['A2'] = title
    sheet['A2'].font = Font(name='Arial', size=14, bold=True, color='222222')
    for col, (name, width) in enumerate(zip(headers, widths), 1):
        cell = sheet.cell(6, col, name)
        cell.fill = PatternFill('solid', fgColor='304C63')
        cell.font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
        cell.alignment = Alignment(vertical='center', horizontal='center', wrap_text=True)
        sheet.column_dimensions[get_column_letter(col)].width = width
    sheet.row_dimensions[6].height = 30
    for row_number, row in enumerate(rows, 7):
        sheet.row_dimensions[row_number].height = 70
        for column, value in enumerate(row, 1):
            cell = sheet.cell(row_number, column)
            literal(cell, value)
            cell.font = Font(name='Arial', size=10, color='222222')
            cell.alignment = Alignment(vertical='center', wrap_text=True)
    if rows:
        table = Table(displayName=table_name, ref=f'A6:{get_column_letter(len(headers))}{len(rows) + 6}')
        table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=False, showColumnStripes=False)
        sheet.add_table(table)
    else:
        sheet.cell(7, 1).alignment = Alignment(vertical='center')
    sheet.freeze_panes = 'A7'


def build_review(data, main_paths):
    workbook = Workbook()
    records = workbook.active
    records.title = '逐条核对'
    validations = workbook.create_sheet('模板校验')
    rows = []
    for record in data['audit']:
        v = record['values']
        sources = '；'.join(f'{c["id"]}：{s["sheet"]} 第{s["row"]}行'
                           for c in record['grid']['candidates'] for s in c['sources'])
        rows.append([data['sources'][record['source']]['name'], record['page'], record['part'], record['excel_row'],
                     record['specimen'], v.get('区县'), v.get('具体地点'),
                     datetime.fromisoformat(v['采集日期']) if v.get('采集日期') else None,
                     v.get('采集方式'), v.get('网格号'), record['grid']['edge_distance_m'],
                     '有' if record['identification_label_present'] else '用户确认' if record.get('identification_evidence') else '未见',
                     '；'.join(record['issues']), None, None, sources])
    setup(records, '标本标签人工核对',
          ['来源文件', '来源页/图', '输出分册', 'Excel行', '标本号', '区县', '具体地点', '采集日期', '采集方式',
           '网格号', '距边界(m)', '鉴定标签', '需核对事项', '人工核对结果', '人工备注', '网格来源'],
          [32, 12, 11, 10, 23, 20, 42, 15, 13, 11, 17, 15, 85, 18, 45, 80], rows, 'SpecimenReview')
    counts = {kind: sum(any(f['kind'] == kind for f in r['flags']) for r in data['audit'])
              for kind in ['near_edge', 'identification']}
    unmatched = sum(r['grid']['status'] != 'matched' for r in data['audit'])
    summary = data['validation_summary']
    records['A3'] = (f'{data["record_count"]}条；近边界提示{counts["near_edge"]}条；缺鉴定标签{counts["identification"]}条；'
                     f'网格留空{unmatched}条；模板冲突{summary["conflict"]}处。')
    records['A4'] = '近边界阈值为复核提示；核对清单为生成时快照，修改主表后需重新校验。'
    records['A5'] = '；'.join(f'分册{part["index"]}：{path.name}' for part, path in zip(data['parts'], main_paths))
    records.freeze_panes = 'F7'
    for number, record in enumerate(data['audit'], 7):
        records.cell(number, 8).number_format = 'yyyy-mm-dd'
        records.cell(number, 11).number_format = '0.00'
        for col in [14, 15]:
            records.cell(number, col).fill = YELLOW
        if any(f['kind'] in ('grid', 'near_edge') for f in record['flags']):
            for col in [10, 11]:
                records.cell(number, col).fill = YELLOW
        if any(f['kind'] == 'identification' for f in record['flags']):
            records.cell(number, 12).fill = YELLOW
    manual = DataValidation(type='list', formula1='"已核对,需修正"', allow_blank=True)
    records.add_data_validation(manual)
    manual.add(f'N7:N{len(rows) + 6}')
    records.conditional_formatting.add(f'N7:N{len(rows) + 6}', CellIsRule(operator='equal', formula=['"需修正"'], fill=PINK))
    labels = {'conflict': '用户规则与模板冲突', 'invalid': '不符合模板', 'unsupported': '尚未自动验证'}
    problems = [c for c in data['validation_checks'] if c['status'] != 'passed']
    validation_rows = []
    for check in problems:
        record = data['audit'][check['record_index']]
        validation_rows.append([check['part'], check['cell'], check['field'], str(check['value']), labels[check['status']],
                                check['expected'], check['specimen'], data['sources'][record['source']]['name'], record['page'], None])
    setup(validations, '模板合法值检查',
          ['输出分册', '单元格', '字段', '实际值', '结果', '模板要求', '标本号', '来源文件', '来源页/图', '人工处理说明'],
          [12, 13, 18, 22, 28, 80, 23, 32, 12, 45], validation_rows, 'TemplateReview')
    validations['A3'] = (f'已检查{summary["checked"]}个已填单元格：通过{summary["passed"]}，规则冲突{summary["conflict"]}，'
                         f'其他异常{summary["invalid"]}，未支持{summary["unsupported"]}。')
    validations['A4'] = '保留用户值；冲突未解决前不能宣称全部模板验证通过。列表比较去掉首尾空格，保留编号。'
    for number in range(7, len(validation_rows) + 7):
        validations.cell(number, 5).fill = PINK
        validations.cell(number, 10).fill = YELLOW
    correction_data = data.get('label_correction_review', {})
    correction_rows = correction_data.get('rows', [])
    if correction_rows:
        corrections = workbook.create_sheet('标签纠错')
        setup(corrections, '模糊与破损标签纠错', correction_data['headers'], correction_data['widths'], correction_rows, 'LabelCorrections')
        summary = data['label_correction_summary']
        corrections['A3'] = (f'跨标签补全{summary["applied"]}处；证据不足{summary["pending"]}处；参考冲突{summary["ambiguous"]}处；'
                              f'按规则处理{summary["rule_managed"]}处；区间值留空{summary.get("non_scalar",0)}处。全部保留原始读数和人工核对栏。')
        corrections['A4'] = '补全值来自独立清晰标签；参考值冲突时留空。补全不等于人工已确认。'
        corrections.freeze_panes = 'F7'
        for number in range(7, len(correction_rows) + 7):
            corrections.row_dimensions[number].height = 110
            for column in [8, 9]:
                corrections.cell(number, column).number_format = '0.00000000' if correction_rows[number-7][5] in ('经度', '纬度') else '@'
            for column in [9, 14, 15]:
                corrections.cell(number, column).fill = YELLOW
        manual_correction = DataValidation(type='list', formula1='"已核对,需修正"', allow_blank=True)
        corrections.add_data_validation(manual_correction)
        manual_correction.add(f'N7:N{len(correction_rows) + 6}')
    return workbook


def write_preview(main_paths, review_path, data, output):
    """Show values reread from saved files; this is not a native Excel renderer."""
    sections = []
    for path in [*main_paths, review_path]:
        workbook = load_workbook(path, read_only=True)
        sheets = [workbook[data['sheet']]] if path != review_path else workbook.worksheets
        for sheet in sheets:
            if path == review_path:
                header, last, columns = 6, min(sheet.max_row, 16), range(1, min(sheet.max_column, 16) + 1)
            else:
                header, last = 1, min(data['record_count'] + 1, 11)
                columns = [data['headers'].index(name) + 1 for name in
                           ['标本号', '采集号', '网格号', '经度', '纬度', '采集日期', '馆名', '保藏状况', '备注']]
            rows = []
            for row in sheet.iter_rows(min_row=header, max_row=last):
                cells = []
                for col in columns:
                    value = row[col - 1].value
                    if isinstance(value, datetime):
                        value = value.date().isoformat()
                    element = 'th' if row[0].row == header else 'td'
                    cells.append(f'<{element}>{html.escape("" if value is None else str(value))}</{element}>')
                rows.append('<tr>' + ''.join(cells) + '</tr>')
            sections.append(f'<h2>{html.escape(path.name)} / {html.escape(sheet.title)}</h2><div class="scroll"><table>' + ''.join(rows) + '</table></div>')
        workbook.close()
    output.write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>标本入库数据预览</title>'
                      '<style>body{font:14px system-ui;margin:24px;color:#222}h2{margin-top:32px}.scroll{overflow:auto}'
                      'table{border-collapse:collapse}th,td{border:1px solid #ddd;padding:9px;min-width:90px;max-width:480px;white-space:pre-wrap}'
                      'th{background:#304c63;color:white}td{vertical-align:top}</style><h1>保存后数据预览</h1>'
                      '<p>此页面从已保存 Excel 读回内容，用于核对文字和数值；不模拟 Excel 原生排版。</p>' + ''.join(sections) + '</html>', encoding='utf-8')


def build(template, data, output, review, evidence_dir):
    template, output, review, evidence_dir = map(Path, (template, output, review, evidence_dir))
    if template.suffix.lower() != '.xlsx' or output.suffix.lower() != '.xlsx' or review.suffix.lower() != '.xlsx':
        raise ValueError('This backend accepts .xlsx files')
    if not data.get('rows') or not data.get('parts'):
        raise ValueError('No compiled records')
    if len(data['rows']) != data['record_count'] or len(data['audit']) != data['record_count']:
        raise ValueError('Compiled record count mismatch')
    next_start = 0
    for index, part in enumerate(data['parts'], 1):
        if part['index'] != index or part['start'] != next_start or part['count'] < 1:
            raise ValueError('Invalid output part mapping')
        next_start += part['count']
    if next_start != data['record_count']:
        raise ValueError('Output parts do not cover every record')
    for row, record in zip(data['rows'], data['audit']):
        if row != [record['values'].get(field) for field in data['headers']]:
            raise ValueError('Compiled matrix and audit values differ')
    original_hash = digest(template)
    if original_hash != digest(data['template']):
        raise ValueError('Selected reference differs from the compiled template')
    tables = read_xlsx(template)
    headers = next(t['rows'][0][1] for t in tables if t['sheet'] == data['sheet'])
    if headers != data['headers']:
        raise ValueError('Template headers changed')
    main_paths = [part_path(output, p['index'], len(data['parts'])) for p in data['parts']]
    paths = [*main_paths, review]
    resolved = [p.resolve() for p in paths]
    if len(set(resolved)) != len(paths) or template.resolve() in resolved:
        raise ValueError('Outputs must differ from one another and the reference')
    for path in paths:
        if path.exists():
            raise FileExistsError(f'Output already exists: {path}')
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = []
    evidence_path = evidence_dir / 'template-copy-evidence.json'
    if evidence_path.exists():
        raise FileExistsError('Choose a new evidence directory')
    for part, destination in zip(data['parts'], main_paths):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with template.open('rb') as source, destination.open('xb') as target:
            shutil.copyfileobj(source, target)
        copied_hash = digest(destination)
        if copied_hash != original_hash:
            raise ValueError('Template copy differs before filling')
        record = {'part': part['index'], 'path': str(destination.resolve()), 'templateSha256': original_hash,
                  'copiedSha256': copied_hash, 'copiedBeforeFilling': True}
        evidence.append(record)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        record['preservation'] = fill_copy(destination, data, part)
        record['filledSha256'] = digest(destination)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    review.parent.mkdir(parents=True, exist_ok=True)
    review_book = build_review(data, main_paths)
    with review.open('xb') as stream:
        review_book.save(stream)
    review_book.close()
    if digest(template) != original_hash:
        raise ValueError('Original template changed')
    write_preview(main_paths, review, data, evidence_dir / 'preview.html')
    return {'main': [str(p) for p in main_paths], 'review': str(review), 'records': data['record_count'],
            'validation': data['validation_summary'], 'templateCopiedBeforeFilling': True,
            'templateSha256': original_hash, 'backend': 'portable-ooxml', 'copyEvidence': str(evidence_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['template', 'compiled', 'output', 'review', 'evidence_dir']:
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    data = json.loads(args.compiled.read_text(encoding='utf-8'))
    print(json.dumps(build(args.template, data, args.output, args.review, args.evidence_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
