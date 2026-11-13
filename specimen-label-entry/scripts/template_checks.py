#!/usr/bin/env python3
"""Read native XLSX validations; inspect populated values without changing user rules."""
from collections import Counter
from datetime import date
import math
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
RID='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


class UnsupportedValidation(ValueError):
    pass


def column_number(letters):
    result=0
    for char in letters:
        result=result*26+ord(char)-64
    return result-1


def address(col,row):
    letters=''
    col+=1
    while col:
        col,remainder=divmod(col-1,26)
        letters=chr(65+remainder)+letters
    return f'{letters}{row}'


def bounds(ref):
    match=re.fullmatch(r'\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?',ref)
    if not match:
        raise UnsupportedValidation(f'不支持的单元格范围：{ref}')
    c0,r0,c1,r1=match.groups()
    result=(column_number(c0),int(r0),column_number(c1 or c0),int(r1 or r0))
    if result[0]>result[2] or result[1]>result[3]:
        raise UnsupportedValidation(f'倒置的单元格范围：{ref}')
    return result


def read_contract(path,sheet_name,column_count):
    with ZipFile(path) as archive:
        workbook=ET.fromstring(archive.read('xl/workbook.xml'))
        sheets=workbook.findall('s:sheets/s:sheet',NS)
        index=next(i for i,s in enumerate(sheets) if s.attrib['name']==sheet_name)
        sheet=sheets[index]
        rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        target=rels[sheet.attrib[RID]]
        target=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        root=ET.fromstring(archive.read(target))
        names={}
        for item in workbook.findall('s:definedNames/s:definedName',NS):
            if item.attrib.get('localSheetId') is None:
                names[item.attrib['name']]=item.text or ''
        for item in workbook.findall('s:definedNames/s:definedName',NS):
            if item.attrib.get('localSheetId')==str(index):
                names[item.attrib['name']]=item.text or ''
        rules=[]
        capacities=[]
        for i,item in enumerate(root.findall('s:dataValidations/s:dataValidation',NS),1):
            ranges=[bounds(ref) for ref in item.attrib['sqref'].split()]
            for c0,r0,c1,r1 in ranges:
                if c0<column_count and r0<=2 and r1>=2:
                    capacities.append(r1-1)
            rules.append({'id':i,'type':item.attrib.get('type','none'),
                          'operator':item.attrib.get('operator','between'),
                          'ranges':ranges,'sqref':item.attrib['sqref'],
                          'formula1':item.findtext('s:formula1','',NS),
                          'formula2':item.findtext('s:formula2','',NS)})
        props=workbook.find('s:workbookPr',NS)
        date1904=props is not None and props.attrib.get('date1904','0') in ('1','true')
    return {'sheet':sheet_name,'rules':rules,'names':names,'date1904':date1904,
            'max_records_per_file':min(capacities) if capacities else 1048575}


def plan_parts(count,capacity):
    if not isinstance(capacity,int) or isinstance(capacity,bool) or capacity<1:
        raise ValueError('每个文件的容量必须是正整数')
    return [{'index':i//capacity+1,'start':i,'count':min(capacity,count-i)} for i in range(0,count,capacity)]


def resolve_expression(expression,contract,tables,seen=()):
    value=(expression or '').strip()
    if value.startswith('='):
        value=value[1:].strip()
    if re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?',value):
        number=float(value)
        if not math.isfinite(number):
            raise UnsupportedValidation('范围表达式不是有限数值')
        return [number]
    if value in contract['names']:
        if value in seen:
            raise UnsupportedValidation('命名范围循环引用')
        return resolve_expression(contract['names'][value],contract,tables,seen+(value,))
    if '!' in value:
        sheet,ref=value.rsplit('!',1)
        sheet=sheet.strip("'").replace("''", "'")
        if '[' in sheet or ']' in sheet:
            raise UnsupportedValidation('外部工作簿引用需要人工核查')
    else:
        sheet,ref=contract['sheet'],value
    c0,r0,c1,r1=bounds(ref)
    if (c1-c0+1)*(r1-r0+1)>10000 or c1>=256:
        raise UnsupportedValidation('引用范围超过当前检查范围')
    if sheet not in tables:
        raise UnsupportedValidation(f'找不到引用工作表：{sheet}')
    rows=dict(tables[sheet])
    return [rows.get(r,[])[c] if c<len(rows.get(r,[])) else None for r in range(r0,r1+1) for c in range(c0,c1+1)]


def date_serial(value,date1904=False):
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        return float(value)
    if isinstance(value,str):
        parsed=date.fromisoformat(value)
        base=date(1904,1,1) if date1904 else date(1899,12,30)
        return (parsed-base).days
    raise ValueError('日期不是 ISO 日期或 Excel 序列数')


def check_rule(value,rule,contract,tables):
    kind=rule['type']
    if kind in ('none',''):
        return True,'无约束'
    if kind=='list':
        formula=rule['formula1'].strip()
        if formula.startswith('"') and formula.endswith('"'):
            choices=[part.strip() for part in formula[1:-1].split(',')]
        else:
            choices=[str(x).strip() for x in resolve_expression(formula,contract,tables) if x is not None and x!='']
        if not choices:
            raise UnsupportedValidation('列表没有可用选项')
        return str(value).strip() in choices,'选项：'+' / '.join(choices)
    if kind not in ('whole','decimal','date','textLength'):
        raise UnsupportedValidation(f'尚不支持的验证类型：{kind}')
    if kind=='textLength':
        number=len(str(value))
    elif kind=='date':
        try:
            number=date_serial(value,contract['date1904'])
        except (TypeError,ValueError):
            return False,'需要有效日期'
    else:
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            return False,'需要有限数值'
        number=float(value)
        if kind=='whole' and not number.is_integer():
            return False,'需要整数'
    def operand(name):
        values=resolve_expression(rule[name],contract,tables)
        if len(values)!=1 or isinstance(values[0],bool) or not isinstance(values[0],(int,float)):
            raise UnsupportedValidation(f'{name} 不能解析为一个数值')
        return values[0]
    low=operand('formula1')
    operator=rule['operator']
    high=operand('formula2') if operator in ('between','notBetween') else None
    tests={'between':lambda:low<=number<=high,'notBetween':lambda:not low<=number<=high,
           'equal':lambda:number==low,'notEqual':lambda:number!=low,
           'lessThan':lambda:number<low,'lessThanOrEqual':lambda:number<=low,
           'greaterThan':lambda:number>low,'greaterThanOrEqual':lambda:number>=low}
    if operator not in tests:
        raise UnsupportedValidation(f'尚不支持的运算符：{operator}')
    expected=f'{kind} {operator} {low:g}'+(f' … {high:g}' if high is not None else '')
    return tests[operator](),expected


def check_values(values,headers,excel_row,contract,tables,fixed):
    results=[]
    for rule in contract['rules']:
        columns=set()
        for c0,r0,c1,r1 in rule['ranges']:
            if r0<=excel_row<=r1:
                columns.update(range(c0,min(c1+1,len(headers))))
        for col in sorted(columns):
            field=headers[col]
            value=values.get(field)
            if value is None or value=='':
                continue  # Unknown observations remain blank; requiredness is handled separately.
            try:
                valid,expected=check_rule(value,rule,contract,tables)
                status='passed' if valid else 'conflict' if field in fixed else 'invalid'
            except UnsupportedValidation as error:
                status,expected='unsupported',str(error)
            results.append({'cell':address(col,excel_row),'column_index':col,'field':field,'value':value,
                            'status':status,'expected':expected,'rule_id':rule['id']})
    return results


def summarize(checks):
    counts={key:0 for key in ('passed','conflict','invalid','unsupported')}
    counts.update(Counter(check['status'] for check in checks))
    return {'checked':len(checks),**counts}
