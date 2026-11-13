#!/usr/bin/env python3
"""Fill only worksheet cells and styles inside an existing XLSX copy.

Other ZIP members retain their original bytes. lxml preserves namespace prefixes,
including those used by Excel extension attributes; the template is never saved
through a library that reconstructs its workbook-level XML.
"""
from copy import deepcopy
from datetime import date
import hashlib
import math
from pathlib import Path
import posixpath
import re
import tempfile
from zipfile import ZipFile
from lxml import etree as E

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = {'s': S}
TAXONOMY = ['中文名称', '门', 'phylum', '纲', 'class', '目', 'order', '中文科名',
            '科名', '属名', '种名', '亚种名', '作者', '年代', '鉴定人', '鉴定日期']
WIDTHS = {'标本号': 22, '采集号': 22, '网格号': 11, '省': 13, '省代码': 11,
          '地级市': 16, '区县': 20, '具体地点': 42, '经度': 18, '纬度': 18,
          '海拔': 12, '生境': 16, '采集方式': 12, '采集人': 32, '采集日期': 15,
          '鉴定人': 18, '鉴定日期': 15, '馆名': 38, '单位代码': 17,
          '标本属性': 13, '保藏方式': 20, '保藏介质': 17, '保藏状况': 13, '备注': 90}


def tag(name):
    return f'{{{S}}}{name}'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def col_name(index):
    result = ''
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def worksheet_member(archive, sheet):
    book = E.fromstring(archive.read('xl/workbook.xml'))
    rels = E.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
    targets = {item.get('Id'): item.get('Target') for item in rels}
    for item in book.findall('s:sheets/s:sheet', NS):
        if item.get('name') == sheet:
            target = targets[item.get(f'{{{R}}}id')]
            return target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
    raise ValueError(f'Template has no worksheet {sheet!r}')


def assert_empty_target(sheet, column_count):
    for cell in sheet.findall('s:sheetData/s:row/s:c', NS):
        match = re.fullmatch(r'([A-Z]+)([0-9]+)', cell.get('r', ''))
        if not match or int(match[2]) < 2:
            continue
        column = 0
        for char in match[1]:
            column = column * 26 + ord(char) - 64
        if column > column_count:
            continue
        if cell.find('s:f', NS) is not None or cell.findtext('s:v', '', NS) != '' or any(
                text.text for text in cell.findall('.//s:t', NS)):
            raise ValueError(f'Template target is populated at {cell.get("r")}; choose an empty reference template')


class Styles:
    """Append cell styles without changing existing style indices or extensions."""

    def __init__(self, root):
        self.root = root
        self.xfs = root.find('s:cellXfs', NS)
        self.fills = root.find('s:fills', NS)
        if self.xfs is None or self.fills is None:
            raise ValueError('Template is missing cell styles')
        num_fmts = root.find('s:numFmts', NS)
        if num_fmts is None:
            num_fmts = E.Element(tag('numFmts'), count='0')
            root.insert(0, num_fmts)
        self.num_fmts = num_fmts
        self.formats = {x.get('formatCode'): int(x.get('numFmtId')) for x in num_fmts}
        self.formats.update({'@': 49, '0': 1, '0.00': 2})
        self.cache = {}
        self.colors = {}

    def number_format(self, code):
        if code not in self.formats:
            index = max([163] + [int(x.get('numFmtId')) for x in self.num_fmts]) + 1
            E.SubElement(self.num_fmts, tag('numFmt'), numFmtId=str(index), formatCode=code)
            self.num_fmts.set('count', str(len(self.num_fmts)))
            self.formats[code] = index
        return self.formats[code]

    def fill(self, color):
        if color not in self.colors:
            index = len(self.fills)
            fill = E.SubElement(self.fills, tag('fill'))
            pattern = E.SubElement(fill, tag('patternFill'), patternType='solid')
            E.SubElement(pattern, tag('fgColor'), rgb='FF' + color)
            E.SubElement(pattern, tag('bgColor'), indexed='64')
            self.fills.set('count', str(len(self.fills)))
            self.colors[color] = index
        return self.colors[color]

    def cell(self, base, number_format, wrap, color):
        key = base, number_format, wrap, color
        if key not in self.cache:
            xf = deepcopy(self.xfs[base])
            xf.set('numFmtId', str(self.number_format(number_format)))
            xf.set('applyNumberFormat', '1')
            alignment = xf.find('s:alignment', NS)
            if alignment is None:
                alignment = E.SubElement(xf, tag('alignment'))
            alignment.set('vertical', 'center')
            if wrap:
                alignment.set('wrapText', '1')
            xf.set('applyAlignment', '1')
            if color:
                xf.set('fillId', str(self.fill(color)))
                xf.set('applyFill', '1')
            self.cache[key] = len(self.xfs)
            self.xfs.append(xf)
            self.xfs.set('count', str(len(self.xfs)))
        return self.cache[key]


def set_width(sheet, index, width):
    cols = sheet.find('s:cols', NS)
    if cols is None:
        cols = E.Element(tag('cols'))
        sheet.insert(list(sheet).index(sheet.find('s:sheetData', NS)), cols)
    for current in list(cols):
        low, high = int(current.get('min')), int(current.get('max'))
        if low <= index <= high:
            position = list(cols).index(current)
            cols.remove(current)
            pieces = []
            if low < index:
                left = deepcopy(current)
                left.set('max', str(index - 1))
                pieces.append(left)
            middle = deepcopy(current)
            middle.set('min', str(index))
            middle.set('max', str(index))
            middle.set('width', str(width))
            middle.set('customWidth', '1')
            pieces.append(middle)
            if index < high:
                right = deepcopy(current)
                right.set('min', str(index + 1))
                pieces.append(right)
            for offset, piece in enumerate(pieces):
                cols.insert(position + offset, piece)
            return
    E.SubElement(cols, tag('col'), min=str(index), max=str(index), width=str(width), customWidth='1')
    cols[:] = sorted(cols, key=lambda x: int(x.get('min')))


def write_value(cell, value, field):
    for child in list(cell):
        if E.QName(child).localname in ('f', 'v', 'is'):
            cell.remove(child)
    cell.attrib.pop('t', None)
    if value is None:
        return
    if field in ('采集日期', '鉴定日期'):
        value = date.fromisoformat(value).isoformat()
        cell.set('t', 'd')
        E.SubElement(cell, tag('v')).text = value + 'T00:00:00'
    elif isinstance(value, bool):
        cell.set('t', 'b')
        E.SubElement(cell, tag('v')).text = '1' if value else '0'
    elif isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError(f'Non-finite number at {cell.get("r")}')
        E.SubElement(cell, tag('v')).text = str(value)
    else:
        cell.set('t', 'inlineStr')
        text = E.SubElement(E.SubElement(cell, tag('is')), tag('t'))
        text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        text.text = str(value)


def serialize(element):
    return E.tostring(element, encoding='UTF-8', xml_declaration=True, standalone=True)


def fill_copy(path, data, part):
    """Fill an already-created template copy and report unchanged package members."""
    with ZipFile(path) as archive:
        member = worksheet_member(archive, data['sheet'])
        sheet = E.fromstring(archive.read(member))
        styles = E.fromstring(archive.read('xl/styles.xml'))
        assert_empty_target(sheet, len(data['headers']))
        style_pool = Styles(styles)
        sheet_data = sheet.find('s:sheetData', NS)
        rows = {int(row.get('r')): row for row in sheet_data}
        fallback = {re.sub(r'[0-9]', '', c.get('r')): c.get('s', '0') for c in rows.get(2, [])}
        for offset, record in enumerate(data['audit'][part['start']:part['start'] + part['count']], 2):
            row = rows.get(offset)
            if row is None:
                row = E.Element(tag('row'), r=str(offset))
                sheet_data.append(row)
                rows[offset] = row
            cells = {re.sub(r'[0-9]', '', c.get('r')): c for c in row}
            colors = {}
            for flag in record['flags']:
                fields = TAXONOMY if flag['kind'] == 'identification' else [flag['field']] if flag.get('field') else []
                for field in fields:
                    color = 'FCE8E6' if flag['kind'] in ('template_conflict', 'template_invalid') else 'FFF2CC'
                    if colors.get(field) != 'FCE8E6':
                        colors[field] = color
            for column, field in enumerate(data['headers'], 1):
                name = col_name(column)
                cell = cells.get(name)
                if cell is None:
                    cell = E.SubElement(row, tag('c'), r=f'{name}{offset}')
                    cells[name] = cell
                value = record['values'].get(field)
                code = '0.00000000' if field in ('经度', '纬度') else '0.00' if field == '海拔' else (
                    'yyyy-mm-dd' if field in ('采集日期', '鉴定日期') else '0' if field == '年代' else '@')
                if value is not None or field in colors:
                    base = int(cell.get('s', fallback.get(name, '0')))
                    cell.set('s', str(style_pool.cell(base, code, field in ('备注', '具体地点'), colors.get(field))))
                write_value(cell, value, field)
            row[:] = sorted(row, key=lambda c: (len(re.sub(r'[0-9]', '', c.get('r'))), re.sub(r'[0-9]', '', c.get('r'))))
            notes = str(record['values'].get('备注') or '')
            lines = sum(max(1, math.ceil(len(line) / 42)) for line in notes.splitlines())
            row.set('ht', str(min(409, max(52, 15 * lines + 8))))
            row.set('customHeight', '1')
        sheet_data[:] = sorted(sheet_data, key=lambda row: int(row.get('r')))
        for index, field in enumerate(data['headers'], 1):
            if field in WIDTHS and any(r['values'].get(field) is not None for r in data['audit'][part['start']:part['start'] + part['count']]):
                set_width(sheet, index, WIDTHS[field])
        replacements = {member: serialize(sheet), 'xl/styles.xml': serialize(styles)}
        with tempfile.NamedTemporaryFile(dir=Path(path).parent, suffix='.xlsx', delete=False) as stream:
            temporary = Path(stream.name)
        try:
            with ZipFile(temporary, 'w') as result:
                result.comment = archive.comment
                for entry in archive.infolist():
                    result.writestr(deepcopy(entry), replacements.get(entry.filename, archive.read(entry.filename)))
            with ZipFile(temporary) as result:
                if result.testzip():
                    raise ValueError('Written XLSX failed ZIP verification')
                unchanged = [name for name in archive.namelist() if name not in replacements]
                if any(archive.read(name) != result.read(name) for name in unchanged):
                    raise ValueError('An unrelated template component changed')
                before_validation = E.fromstring(archive.read(member)).find('s:dataValidations', NS)
                after_validation = E.fromstring(result.read(member)).find('s:dataValidations', NS)
                validation_preserved = (None if before_validation is None else E.tostring(before_validation)) == (
                    None if after_validation is None else E.tostring(after_validation))
                if not validation_preserved:
                    raise ValueError('Native template validations changed')
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)
    return {'modified_members': sorted(replacements), 'unchanged_members': unchanged,
            'unrelated_members_byte_identical': True, 'native_validations_preserved': validation_preserved}
