#!/usr/bin/env python3
"""Produce a page-level manual review checklist from compiled specimen records."""
import argparse
from collections import Counter
import json
from pathlib import Path


def text(value):
    return str(value if value is not None else '留空').replace('|','\\|').replace('\n',' ')


def coordinate(value):
    return '留空' if value is None else f'{value:.8f}'


def unclear_report(data):
    lines=['# 标签不清晰与区间值明细','','按来源照片/页逐枚列出；区间值可清晰可读，但不填入单值列。', '',
           '| 来源照片/页 | 标本号 | 字段 | 原始读数 | 填写值 | 处理 | 重试记录 |',
           '|---|---|---|---|---|---|---|']
    count=0
    from label_corrections import STATUS_LABELS
    for record in sorted(data['audit'],key=lambda r:(data['sources'][r['source']]['name'],str(r.get('specimen') or ''),r['page'])):
        attempts='、'.join(str(a.get('stage','')) for a in record.get('review_attempts',[])) or '未记录'
        for item in record.get('label_corrections',[]):
            count+=1
            lines.append('| '+' | '.join(text(v) for v in [data['sources'][record['source']]['name']+f' 第{record["page"]}页/图',
                record['specimen'],item['field'] or item.get('label_role'),item['original_value'],
                record['values'].get(item['field']),STATUS_LABELS[item['status']],attempts])+' |')
    if not count:lines+=['','当前编译记录没有标注未解决的标签质量问题或区间值；这不替代原图和覆盖检查。']
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('compiled',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--unclear-out',type=Path,help='Also write photo-by-photo unclear/range observations')
    args=parser.parse_args()
    data=json.loads(args.compiled.read_text())
    if args.unclear_out and (args.unclear_out.resolve() in [args.compiled.resolve(),args.out.resolve()] or args.unclear_out.exists()):
        raise ValueError('Choose a distinct new unclear-detail output')
    records=data['audit']
    matched=sum(r['grid']['status']=='matched' for r in records)
    missing=[r for r in records if any('未见鉴定标签' in s and '留空' in s for s in r['issues'])]
    methods=Counter(r['values']['采集方式'] for r in records)
    rules=data.get('rules_applied',{})
    validation=data.get('validation_summary',{})
    near=[r for r in records if any(f['kind']=='near_edge' for f in r.get('flags',[]))]
    lines=['# 标本标签录入人工核对清单','',
           f"共 {len(records)} 条记录；网格成功匹配 {matched} 条，留空 {len(records)-matched} 条。",
           f"采集方式：{'、'.join(f'{name} {count} 条' for name,count in methods.items())}。仍需人工核对。",'',
           f"模板合法值检查：已检查 {validation.get('checked',0)} 个已填单元格，通过 {validation.get('passed',0)}，用户规则与模板冲突 {validation.get('conflict',0)}，其他异常 {validation.get('invalid',0)}，尚未自动验证 {validation.get('unsupported',0)}。",
           f"近网格边界提示 {len(near)} 条；阈值 {rules.get('grid_review_distance_m')} 米仅用于复核，不代表 GPS 误差。",'',
           '## 需要补充或确认的项目','',
           f"- 按用户确认留空：{'、'.join(rules.get('leave_blank',[]))}。",
           f'- {len(missing)} 条照片未见鉴定标签，学名、中文名称、分类链、作者年代、鉴定人和鉴定日期留空。仅当用户明确确认共享鉴定时补填。']
    for record in records:
        if record['grid']['status']!='matched':
            values=record['values']
            source=data['sources'][record['source']]['name']
            lines.append(f"- {record['specimen']}：{source} 第 {record['page']} 页，{values.get('区县','')}{values.get('具体地点','')}，经度 {values.get('经度')}、纬度 {values.get('纬度')}，网格状态 {record['grid']['status']}。未以最近中心点替填。")
    for message in rules.get('pending_conventions',[]):
        lines.append('- 待确认口径：'+message)
    conflict_groups=Counter((c['field'],str(c['value']),c['expected'],c['status']) for c in data.get('validation_checks',[]) if c['status']!='passed')
    for (field,value,expected,status),count in conflict_groups.items():
        label={'conflict':'用户规则与模板冲突','invalid':'不符合模板','unsupported':'尚未自动验证'}[status]
        lines.append(f'- {label}：{field}={value}，共 {count} 处；{expected}。原值未自动修改。')
    corrections=data.get('label_correction_review',{}).get('rows',[])
    if corrections:
        summary=data['label_correction_summary']
        lines += ['', '## 模糊与破损标签纠错','',
                  f"跨标签补全 {summary['applied']} 处；证据不足 {summary['pending']} 处；参考标签冲突 {summary['ambiguous']} 处；按规则处理 {summary['rule_managed']} 处；区间值留空 {summary.get('non_scalar',0)} 处。均待人工核对。",'',
                  '原始读数与标签原文保留；补全值单独记录来源。参考值不一致时留空，补全字段不作为其他记录的清晰参考。','',
                  '| 标本号 | 字段 | 状况 | 原始读数 | 填写值 | 处理结果 | 参考来源 | 匹配依据 |',
                  '|---|---|---|---|---|---|---|---|']
        for row in corrections:
            lines.append('| '+' | '.join(text(row[i]) for i in [4,5,6,7,8,9,10,11])+' |')
    lines += ['', '## 填写与来源约定','',
              f"- 主表先完整复制参考 Excel，再在副本中填写，保留工作表名和 {len(data['headers'])} 列表头顺序；按原始顺序逐条写入，保留来源页/图索引。",
              '- 经纬度为数值十进制度，显示 8 位小数；海拔为米；采集与鉴定日期为 Excel 日期。',
              '- 地点中的采集方式词单列；同名地点仍按各自标签坐标记录。',
              '- 省代码遵循模板字母编码；直辖市未出现的地级市层级留空。',
              '- 有鉴定标签时，“种名”填完整双名；作者括号与年代分列；鉴定人保留标签写法，未推测中文全名。',
              '- 固定值按本批规则：'+'；'.join(f'{k}“{v}”' for k,v in rules.get('fixed',{}).items())+'。',
              '- 采集号与标本号一致；采集方式按本批已确认规则填写。',
              '- 无依据的测序、任务、寄主、联系方式等字段留空。',
              f"- 网格查询范围包含 {len(data.get('grid_sheets',[]))} 张工作表，按四顶点多边形判定并合并同编号重复来源；下表列出实际命中的表及行。", '',
              f"- 每个主表最多 {data.get('template_contract',{}).get('max_records_per_file','未限制')} 条记录；本批分为 {len(data.get('parts',[]))} 册，确保记录不超出原验证范围。",'',
              '## 网格证据','', '| 网格号 | 记录数 | 来源工作表与 Excel 行 |', '|---|---:|---|']
    grids={}
    for record in records:
        if record['grid']['status']=='matched':
            candidate=record['grid']['candidates'][0]
            grids.setdefault(candidate['id'],{'count':0,'sources':candidate['sources']})['count']+=1
    if data.get('batch_notes'):
        position=lines.index('## 网格证据')
        lines[position:position]=['## 本批说明','']+['- '+text(note) for note in data['batch_notes']]+['']
    for gid,grid in sorted(grids.items()):
        source='；'.join(f"{s['sheet']} 第{s['row']}行" for s in grid['sources'])
        lines.append(f"| {text(gid)} | {grid['count']} | {text(source)} |")
    lines += ['', '## 逐条核对','',
              '| 来源文件 | 来源页/图 | 分册 | Excel 行 | 标本号 | 地点 | 经度 / 纬度 | 海拔 m | 采集日期 | 方式 | 网格 | 距边界 m | 鉴定标签 |',
              '|---|---:|---:|---:|---|---|---|---:|---|---|---|---:|---|']
    for index,record in enumerate(records,2):
        v=record['values']
        identified='有' if record.get('identification_label_present') else '用户确认' if record.get('identification_evidence') else '未见，留空'
        lines.append('| '+' | '.join(map(text,[data['sources'][record['source']]['name'],record['page'],record.get('part',1),record.get('excel_row',index),record['specimen'],f"{v.get('区县','')}{v.get('具体地点','')}",
                     f"{coordinate(v.get('经度'))} / {coordinate(v.get('纬度'))}",v.get('海拔'),v.get('采集日期'),v.get('采集方式'),v.get('网格号'),record['grid'].get('edge_distance_m'),identified]))+' |')
    if data.get('supplemental_sources'):
        lines += ['', '## 外部补充分类的依据','',
                  '外部名称与分类信息只补充到已有可靠学名证据的记录；不传播到缺鉴定依据的记录。']
        for source in data['supplemental_sources']:
            lines.append(f"- {', '.join(source['fields'])}：[{source['locator']}]({source['url']})，核对日期 {source['checked']}。")
    lines += ['', '## 再次使用 skill','',
              '提供新批次 PDF 或照片，然后输入：', '',
              '> 使用 $specimen-label-entry，按已保存规则识别这批标签并填写标本库模板，匹配网格，输出人工核对清单。', '',
              '默认模板、网格和规则均已随 skill 保存；有新规则或参考文件时以新提供的内容为准。', '']
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text('\n'.join(lines))
    if args.unclear_out:
        args.unclear_out.parent.mkdir(parents=True,exist_ok=True)
        args.unclear_out.write_text(unclear_report(data))
    print(args.out)


if __name__=='__main__':
    main()
