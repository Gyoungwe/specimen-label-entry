#!/usr/bin/env python3
"""Read-only verification of exported main workbooks and the Excel review checklist."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from openpyxl import load_workbook
from specimen_data import read_xlsx
from template_checks import check_values, summarize


def part_path(base,index,count):
    return base if count==1 else base.with_name(f'{base.stem}_{index:02d}{base.suffix}')


def signature(sheet):
    return sorted((tuple(sorted(str(v.sqref).split())),v.type,v.operator,v.formula1,v.formula2)
                  for v in sheet.data_validations.dataValidation)


def verify(data,output,review):
    errors=[]
    template=load_workbook(data['template'])
    expected_signature=signature(template[data['sheet']])
    all_checks=[]
    checked_cells=0
    for part in data['parts']:
        path=part_path(output,part['index'],len(data['parts']))
        workbook=load_workbook(path)
        sheet=workbook[data['sheet']]
        if workbook.sheetnames!=template.sheetnames:
            errors.append(f'{path.name} 工作表结构改变')
        if [sheet.cell(1,c+1).value for c in range(len(data['headers']))]!=data['headers']:
            errors.append(f'{path.name} 表头改变')
        if signature(sheet)!=expected_signature:
            errors.append(f'{path.name} 数据验证的范围/类型/公式改变')
        tables={s['sheet']:s['rows'] for s in read_xlsx(path)}
        part_checks=[]
        for local,record in enumerate(data['audit'][part['start']:part['start']+part['count']],2):
            row_values={}
            for col,field in enumerate(data['headers'],1):
                cell=sheet.cell(local,col)
                actual=cell.value
                expected=record['values'].get(field)
                if isinstance(actual,datetime):
                    actual=actual.date().isoformat()
                if isinstance(expected,str) and actual=="'"+expected and expected[:1] in '=+@-':
                    actual=expected
                if actual!=expected:
                    errors.append(f'{path.name}:{cell.coordinate} 导出值与编译记录不一致')
                if field in ('采集日期','鉴定日期') and expected is not None and cell.data_type!='d':
                    errors.append(f'{path.name}:{cell.coordinate} 未保存为 Excel 日期')
                if field in ('标本号','采集号','网格号','单位代码') and expected is not None and cell.data_type!='s':
                    errors.append(f'{path.name}:{cell.coordinate} 标识符未保存为文本')
                if cell.data_type=='f':
                    errors.append(f'{path.name}:{cell.coordinate} 意外的公式')
                row_values[field]=actual
                checked_cells+=1
            checks=check_values(row_values,data['headers'],local,data['template_contract'],tables,data['rules_applied']['fixed'])
            for check in checks:
                check['part']=part['index']
            part_checks.extend(checks)
        for row in sheet.iter_rows(min_row=part['count']+2,max_row=max(sheet.max_row,part['count']+1),min_col=1,max_col=len(data['headers'])):
            if any(c.value is not None for c in row):
                errors.append(f'{path.name} 存在输出记录之外的数据')
                break
        all_checks.extend(part_checks)
    actual_summary=summarize(all_checks)
    if actual_summary!=data['validation_summary']:
        errors.append('导出后的模板检查统计与编译前不一致')
    expected_status=[(c['part'],c['cell'],c['field'],c['status']) for c in data['validation_checks']]
    actual_status=[(c['part'],c['cell'],c['field'],c['status']) for c in all_checks]
    if actual_status!=expected_status:
        errors.append('导出后的模板检查逐项结果与编译前不一致')
    review_book=load_workbook(review)
    if review_book.sheetnames!=['逐条核对','模板校验']:
        errors.append('核对清单缺少要求的工作表')
    else:
        sheet=review_book['逐条核对']
        if sheet.max_row!=data['record_count']+6:
            errors.append('核对清单记录数不一致')
        for row,record in enumerate(data['audit'],7):
            expected=[data['sources'][record['source']]['name'],record['page'],record['part'],record['excel_row'],record['specimen']]
            if [sheet.cell(row,c).value for c in range(1,6)]!=expected:
                errors.append(f'核对清单第{row}行的来源/分册/行号/标本号不一致')
            if sheet.cell(row,11).value!=record['grid']['edge_distance_m']:
                errors.append(f'核对清单第{row}行的边界距离不一致')
            if sheet.cell(row,14).value is not None or sheet.cell(row,15).value is not None:
                errors.append(f'核对清单第{row}行已填入未经人工提供的核对结果')
        validation=review_book['模板校验']
        problems=[c for c in data['validation_checks'] if c['status']!='passed']
        if validation.max_row!=max(7,len(problems)+6):
            errors.append('模板异常明细数量不一致')
        for row,check in enumerate(problems,7):
            if [validation.cell(row,c).value for c in (1,2,3,7)]!=[check['part'],check['cell'],check['field'],check['specimen']]:
                errors.append(f'模板校验第{row}行定位不一致')
    return {'structure_and_value_checks_passed':not errors,'errors':errors,'record_count':data['record_count'],
            'main_workbooks':len(data['parts']),'compared_cells':checked_cells,
            'native_validation_rules_preserved_per_main_file':len(expected_signature),
            'template_validation':actual_summary,
            'near_edge_records':sum(any(f['kind']=='near_edge' for f in r['flags']) for r in data['audit']),
            'missing_identification_records':sum(any(f['kind']=='identification' for f in r['flags']) for r in data['audit']),
            'unmatched_grids':sum(r['grid']['status']!='matched' for r in data['audit'])}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('compiled',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('review',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=verify(json.loads(args.compiled.read_text()),args.output,args.review)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
    if result['errors']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
