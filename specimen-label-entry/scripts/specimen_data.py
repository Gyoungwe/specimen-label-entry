#!/usr/bin/env python3
"""Read XLSX inputs; apply specimen rules and polygon containment. Standard library only."""
import argparse
from collections import Counter
from datetime import date, datetime
import json
import math
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from template_checks import read_contract, check_values, summarize, plan_parts
from label_corrections import repair_batch, correction_message, review_table, collection_text_acceptable

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
SKILL = Path(__file__).resolve().parent.parent


def column_index(address):
    number = 0
    for char in re.match(r"[A-Z]+", address)[0]:
        number = number * 26 + ord(char) - 64
    return number - 1


def read_xlsx(path, max_columns=256):
    """Read values without iterating Excel's spurious formatted XFD range."""
    result = []
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(t.text or "" for t in item.iterfind(".//s:t", NS))
                       for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        rels = {item.attrib["Id"]: item.attrib["Target"] for item in
                ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS):
            target = rels[sheet.attrib[RID]]
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            rows = []
            root = ET.fromstring(archive.read(target))
            for row in root.findall("s:sheetData/s:row", NS):
                values = {}
                for cell in row.findall("s:c", NS):
                    col = column_index(cell.attrib["r"])
                    if col >= max_columns:
                        continue
                    typ = cell.attrib.get("t")
                    value = cell.findtext("s:v", None, NS)
                    if typ == "inlineStr":
                        value = "".join(t.text or "" for t in cell.findall(".//s:t", NS))
                    elif typ == "s" and value is not None:
                        value = strings[int(value)]
                    elif typ == "b" and value is not None:
                        value = value == "1"
                    elif typ == 'd' and value is not None:
                        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                        value=parsed.date().isoformat() if not (parsed.hour or parsed.minute or parsed.second or parsed.microsecond) else parsed.isoformat()
                    elif value is not None and typ not in ("str", "e"):
                        value = float(value)
                        if value.is_integer():
                            value = int(value)
                    if value is not None and value != "":
                        values[col] = value
                if values:
                    row_values = [values.get(i) for i in range(max(values) + 1)]
                    rows.append((int(row.attrib["r"]), row_values))
            result.append({"sheet": sheet.attrib["name"], "rows": rows})
    return result


def load_grids(path, sheets=None):
    grids = {}
    for sheet in read_xlsx(path, max_columns=13):
        if sheets is not None and sheet['sheet'] not in sheets:
            continue
        if not sheet["rows"]:
            continue
        header = sheet["rows"][0][1]
        required = ["编号"] + [f"顶点{i}{axis}" for i in range(1, 5) for axis in ("经度", "纬度")]
        if not all(key in header for key in required):
            continue
        for row_number, row in sheet["rows"][1:]:
            fields = dict(zip(header, row))
            if not fields.get("编号"):
                continue
            grid_id = str(fields["编号"]).strip()
            polygon = [(float(fields[f"顶点{i}经度"]), float(fields[f"顶点{i}纬度"])) for i in range(1, 5)]
            if any(not math.isfinite(x) or not math.isfinite(y) or abs(x) > 180 or abs(y) > 90
                   for x, y in polygon):
                raise ValueError(f"Invalid polygon: {grid_id}")
            source = {"sheet": sheet["sheet"], "row": row_number,
                      "province_city": fields.get('所在省市'), "counties": fields.get('所在县区')}
            if grid_id in grids:
                old = grids[grid_id]["polygon"]
                # Compare all vertices irrespective of start position or orientation.
                if any(not any(abs(x-a) < 1e-10 and abs(y-b) < 1e-10 for a, b in old) for x, y in polygon):
                    raise ValueError(f"Conflicting polygons for grid {grid_id}; resolve the source before export")
                grids[grid_id]["sources"].append(source)
            else:
                grids[grid_id] = {"id": grid_id, "polygon": polygon, "sources": [source]}
    if not grids:
        raise ValueError("No grids with the required four-vertex schema")
    return list(grids.values())


def classify_point(x, y, polygon, tolerance=1e-9):
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        if length2 == 0:
            continue
        fraction = max(0, min(1, ((x-a[0])*dx + (y-a[1])*dy) / length2))
        distance = math.hypot(x-a[0]-fraction*dx, y-a[1]-fraction*dy)
        if distance <= tolerance:
            return "boundary"
        if (a[1] > y) != (b[1] > y):
            cross_x = a[0] + (y-a[1]) * dx / dy
            if x < cross_x:
                inside = not inside
    return "inside" if inside else "outside"


def edge_distance_m(lon, lat, polygon):
    """Local plane approximation, for review priority rather than GPS accuracy."""
    sx,sy=111320*math.cos(math.radians(lat)),110940
    best=float('inf')
    for first,last in zip(polygon,polygon[1:]+polygon[:1]):
        ax,ay=(first[0]-lon)*sx,(first[1]-lat)*sy
        bx,by=(last[0]-lon)*sx,(last[1]-lat)*sy
        dx,dy=bx-ax,by-ay
        length2=dx*dx+dy*dy
        t=max(0,min(1,-(ax*dx+ay*dy)/length2)) if length2 else 0
        best=min(best,math.hypot(ax+t*dx,ay+t*dy))
    return best


def match_grid(lon, lat, grids, tolerance=1e-9):
    if lon is None or lat is None:
        return {"status": "missing_coordinates", "id": None, "candidates": [], "edge_distance_m": None}
    if isinstance(lon, bool) or isinstance(lat, bool) or not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        raise ValueError("Coordinates must be numeric decimal degrees")
    if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("Invalid longitude/latitude; do not swap axes automatically")
    candidates = []
    for grid in grids:
        state = classify_point(lon, lat, grid["polygon"], tolerance)
        if state != "outside":
            candidates.append({"id": grid["id"], "relation": state, "sources": grid["sources"],
                               "edge_distance_m": round(edge_distance_m(lon,lat,grid['polygon']),2)})
    if len(candidates) == 1 and candidates[0]["relation"] == "inside":
        return {"status": "matched", "id": candidates[0]["id"], "candidates": candidates,
                "edge_distance_m": candidates[0]['edge_distance_m']}
    status = "boundary" if any(c["relation"] == "boundary" for c in candidates) else "ambiguous" if candidates else "unmatched"
    return {"status": status, "id": None, "candidates": candidates, "edge_distance_m": None}


def compile_batch(batch, template, grid_file, rules):
    template_sheets = read_xlsx(template)
    target = batch.get("sheet", template_sheets[0]["sheet"])
    sheet = next(s for s in template_sheets if s["sheet"] == target)
    header_row, headers = sheet["rows"][0]
    if header_row != 1 or any(h is None for h in headers) or len(headers) != len(set(headers)):
        raise ValueError("Expected unique contiguous headers in row 1")
    if len(sheet['rows']) > 1:
        raise ValueError("The target template sheet contains data; choose an empty template or agree on an append plan")
    batch, correction_report = repair_batch(batch, rules, headers)
    contract=read_contract(template,target,len(headers))
    capacity=contract['max_records_per_file']
    tables={s['sheet']:s['rows'] for s in template_sheets}
    threshold=rules.get('grid_review_distance_m',200)
    if threshold is not None and (isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or threshold<0):
        raise ValueError('grid_review_distance_m must be a nonnegative finite number or null')
    grids = load_grids(grid_file, rules.get('grid_sheets'))
    seen, covered = set(), Counter()
    matrix, audit, validation_checks = [], [], []
    sources = batch["sources"]
    for record_index,record in enumerate(batch["records"]):
        source = sources[record["source"]]
        page = record["page"]
        if isinstance(page, bool) or not isinstance(page, int) or not (1 <= page <= source["page_count"]):
            raise ValueError("Invalid source page")
        covered[(record["source"], page)] += 1
        if not record.get("visually_checked", False):
            raise ValueError(f"Page {page}: visual review is still required")
        values = dict(record["values"])
        if set(values) - set(headers):
            raise ValueError(f"Unknown template fields: {set(values) - set(headers)}")
        sid = values.get("标本号")
        if sid and sid in seen:
            raise ValueError(f"Duplicate specimen number: {sid}; inspect instead of dropping a row")
        if sid:
            seen.add(sid)
        for name in ("采集日期", "鉴定日期"):
            if values.get(name):
                date.fromisoformat(values[name])
        values.update(rules["fixed"])
        for key in rules["leave_blank"]:
            values[key] = None
        if rules["collection_number_equals_specimen_number"]:
            values["采集号"] = sid
        method = rules["collection_method"]
        raw = record.get("collection_label_text")
        if not collection_text_acceptable(record):
            raise ValueError(f"Page {page}: collection_label_text is required")
        values["采集方式"] = method["when_present"] if method["literal_marker"] in (raw or '') else method["otherwise"]
        if values.get("省") in rules["province_codes"]:
            values["省代码"] = rules["province_codes"][values["省"]]
        grid = match_grid(values.get("经度"), values.get("纬度"), grids,
                          rules["grid_boundary_tolerance_degrees"])
        values["网格号"] = grid["id"]
        issues = list(record.get("issues", []))
        flags=[]
        def flag(kind,field,message):
            issues.append(message)
            flags.append({'kind':kind,'field':field,'message':message})
        for item in record.get('label_corrections', []):
            flag('label_correction' if item['status'] == 'applied' else 'label_quality', item['field'], correction_message(item))
        if not sid:
            flag('missing','标本号',"标本号缺失/待核对")
        if grid["status"] != "matched":
            flag('grid','网格号',"网格待核对：" + grid["status"])
        else:
            if threshold is not None and grid['edge_distance_m']<threshold:
                flag('near_edge','网格号',f"距网格边界约{grid['edge_distance_m']:.2f}米，低于{threshold:g}米复核阈值")
            county=values.get('区县')
            counties=set()
            for source_row in grid['candidates'][0]['sources']:
                counties.update(p.strip() for p in re.split(r'[、，,；;\s]+',source_row.get('counties') or '') if p.strip())
            if rules.get('check_county_grid',True) and county and counties and county not in counties:
                flag('county','区县',f"标签区县{county}未见于网格区县列表：{'、'.join(sorted(counties))}")
        if record.get("identification_label_present") is False:
            evidence = record.get("identification_evidence") or {}
            confirmed = evidence.get("type") == "user_confirmation" and bool(evidence.get("statement", "").strip())
            fields = ("中文名称", "门", "phylum", "纲", "class", "目", "order", "中文科名", "科名", "属名", "种名", "亚种名", "作者", "年代", "鉴定人", "鉴定日期")
            if any(values.get(name) for name in fields) and not confirmed:
                raise ValueError(f"Page {page}: no identification label or explicit user confirmation but identification fields were populated")
            if confirmed:
                issues.append('照片未见鉴定标签；按用户明确确认填写')
            else:
                flag('identification',None,'照片未见鉴定标签；学名及鉴定信息留空')
        part=record_index//capacity+1
        excel_row=record_index%capacity+2
        checks=check_values(values,headers,excel_row,contract,tables,rules['fixed'])
        for check in checks:
            check.update({'record_index':record_index,'part':part,'excel_row':excel_row,'specimen':sid})
            if check['status']!='passed':
                wording={'conflict':f"{check['field']}：用户规则值“{check['value']}”与模板冲突；保留原值",
                         'invalid':f"{check['field']}：不符合模板校验，需核对",
                         'unsupported':f"{check['field']}：模板公式尚未自动验证，需人工核对"}[check['status']]
                flag('template_'+check['status'],check['field'],wording)
        validation_checks.extend(checks)
        issues=list(dict.fromkeys(issues))
        provenance = f"来源：{source['name']} 图组{record['group_id']}" if record.get('group_id') else f"来源：{source['name']} 第{page}页"
        values["备注"] = "；".join(filter(None, [values.get("备注"), provenance] + issues))
        matrix.append([values.get(h) for h in headers])
        audit.append({"source": record["source"], "page": page, "specimen": sid,
                      "record_id":record.get('record_id'),"label_quality":record.get('label_quality',{}),
                      "field_quality":record.get('field_quality',{}),"observed_values":record.get('observed_values'),
                      "collection_label_text":record.get('collection_label_text'),
                      "identification_label_text":record.get('identification_label_text'),
                      "label_corrections":record.get('label_corrections',[]),
                      "interval_observations":record.get('interval_observations',{}),
                      "review_attempts":record.get('review_attempts',[]),
                      "group_id":record.get('group_id'),"image_provenance":record.get('image_provenance'),
                      "part":part,"excel_row":excel_row,"flags":flags,
                      "identification_label_present": record.get("identification_label_present"),
                      "identification_evidence": record.get("identification_evidence"),
                      "grid": grid, "issues": issues, "values": values})
    omissions = []
    for source_key, source in sources.items():
        excluded = {entry["page"] for entry in source.get("excluded_pages", []) if entry.get("reason")}
        for page in range(1, source["page_count"] + 1):
            if not covered[(source_key, page)] and page not in excluded:
                omissions.append([source_key, page])
    if omissions:
        raise ValueError(f"Pages missing from records and explicit exclusions: {omissions}")
    if not matrix:
        raise ValueError('No specimen records to export')
    return {"sheet": target, "headers": headers, "rows": matrix, "audit": audit,
            "template":str(Path(template).resolve()),"template_contract":contract,
            "parts":plan_parts(len(matrix),capacity),
            "validation_checks":validation_checks,"validation_summary":summarize(validation_checks),
            "sources": sources, "supplemental_sources": batch.get("supplemental_sources", []),
            "batch_notes": batch.get('batch_notes', []),
            "rules_applied": rules,
            "grid_sheets": list(dict.fromkeys(source['sheet'] for grid in grids for source in grid['sources'])),
            "record_count": len(matrix), "unique_grids": len(grids),
            "label_correction_summary":correction_report['summary'],
            "label_correction_review":review_table(audit, sources)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument("--template", type=Path, default=SKILL / "assets/标本库_模板.xlsx")
    parser.add_argument("--grids", type=Path, default=SKILL / "assets/核心网格.xlsx")
    parser.add_argument("--rules", type=Path, default=SKILL / "assets/rules.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    data = compile_batch(json.loads(args.records.read_text()), args.template, args.grids,
                         json.loads(args.rules.read_text()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({"records": data["record_count"], "grid_status": dict(Counter(r["grid"]["status"] for r in data["audit"])),
                      "grid_ids": dict(Counter(r["grid"]["id"] for r in data["audit"]))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
