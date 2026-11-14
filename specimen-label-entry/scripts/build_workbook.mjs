// Copy beside a node_modules symlink to the bundled runtime before running.
import fs from 'node:fs/promises';
import { constants as fsConstants } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { FileBlob, SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const [template, compiledPath, output, reviewOutput, previewDirectory] = process.argv.slice(2);
if (!template || !compiledPath || !output || !reviewOutput || !previewDirectory) {
  throw new Error('Usage: node build_workbook.mjs TEMPLATE.xlsx COMPILED.json OUTPUT.xlsx REVIEW.xlsx PREVIEW_DIR');
}
const data = JSON.parse(await fs.readFile(compiledPath, 'utf8'));
if (!data.rows.length || !data.parts?.length) throw new Error('No compiled records');
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const templateSha256 = sha256(await fs.readFile(template));
const copyEvidence = [];
const mainPath = part => data.parts.length === 1 ? output : output.replace(/\.xlsx$/i, `_${String(part).padStart(2, '0')}.xlsx`);
const destinations = [...data.parts.map(part => mainPath(part.index)), reviewOutput];
if (new Set(destinations.map(p => path.resolve(p))).size !== destinations.length) throw new Error('Output paths must differ');
for (const destination of destinations) {
  if (path.resolve(destination) === path.resolve(template)) throw new Error('Choose a new output file');
  try { await fs.access(destination); throw new Error(`Output already exists: ${destination}`); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
}
const safeText = value => typeof value === 'string' && /^[=+@-]/.test(value) ? `'${value}` : value;
const literal = value => typeof value === 'number' ? String(value) : `"${String(value).replaceAll('"', '""')}"`;
const yellow = '#FFF2CC';
const pink = '#FCE8E6';
const taxonomy = ['中文名称','门','phylum','纲','class','目','order','中文科名','科名','属名','种名','亚种名','作者','年代','鉴定人','鉴定日期'];
const widths = {
  '中文名称':18,'phylum':16,'纲':12,'class':12,'目':12,'order':15,'中文科名':12,'科名':16,
  '属名':18,'种名':30,'作者':14,'年代':9,'标本号':22,'采集号':22,'网格号':11,'国家':10,
  '省':13,'省代码':11,'地级市':16,'区县':20,'具体地点':42,'经度':18,'纬度':18,'海拔':12,
  '生境':16,'采集方式':12,'采集人':42,'采集日期':15,'鉴定人':18,'鉴定日期':15,'馆名':38,
  '单位代码':17,'标本属性':13,'保藏方式':20,'保藏介质':17,'保藏状况':13,'备注':90
};
await fs.mkdir(previewDirectory, {recursive:true});
async function preview(workbook, sheetName, name, range) {
  const blob = await workbook.render({sheetName,range,scale:1.2,format:'png'});
  await fs.writeFile(path.join(previewDirectory, `${name}.png`),new Uint8Array(await blob.arrayBuffer()));
}

for (const part of data.parts) {
  const destination = mainPath(part.index);
  await fs.mkdir(path.dirname(destination),{recursive:true});
  await fs.copyFile(template,destination,fsConstants.COPYFILE_EXCL);
  const copiedSha256 = sha256(await fs.readFile(destination));
  if (copiedSha256 !== templateSha256) throw new Error('Template copy differs before filling');
  const evidence = {part:part.index,path:destination,templateSha256,copiedSha256,copiedBeforeFilling:true};
  copyEvidence.push(evidence);
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(destination));
  const sheet = workbook.worksheets.getItem(data.sheet);
  const headers = sheet.getRangeByIndexes(0,0,1,data.headers.length).values[0];
  if (JSON.stringify(headers) !== JSON.stringify(data.headers)) throw new Error('Template headers changed');
  const existing = sheet.getRangeByIndexes(1,0,part.count,headers.length).values;
  if (existing.some(row => row.some(value => value !== null && value !== ''))) throw new Error('Template target is populated');
  const matrix = data.rows.slice(part.start,part.start+part.count).map(row => row.map((value,col) => {
    if (value == null) return null;
    return ['采集日期','鉴定日期'].includes(headers[col]) ? new Date(`${value}T00:00:00Z`) : safeText(value);
  }));
  sheet.getRangeByIndexes(1,0,matrix.length,headers.length).values = matrix;
  for (let col=0;col<headers.length;col++) {
    if (!matrix.some(row => row[col] != null)) continue;
    const range=sheet.getRangeByIndexes(1,col,matrix.length,1);
    range.format.verticalAlignment='center';
    if (widths[headers[col]]) sheet.getRangeByIndexes(0,col,matrix.length+1,1).format.columnWidth=widths[headers[col]];
    if (['经度','纬度'].includes(headers[col])) range.setNumberFormat('0.00000000');
    else if (headers[col]==='海拔') range.setNumberFormat('0.00');
    else if (['采集日期','鉴定日期'].includes(headers[col])) range.setNumberFormat('yyyy-mm-dd');
    else range.setNumberFormat(headers[col]==='年代' ? '0' : '@');
  }
  for (const record of data.audit.slice(part.start,part.start+part.count)) {
    const row=record.excel_row-1;
    for (const flag of record.flags) {
      const fields=flag.kind==='identification' ? taxonomy : flag.field ? [flag.field] : [];
      for (const field of fields) {
        const col=headers.indexOf(field);
        if (col<0) continue;
        const cell=sheet.getRangeByIndexes(row,col,1,1);
        if (['template_conflict','template_invalid'].includes(flag.kind)) {
          const ref=data.validation_checks.find(check => check.record_index===part.start+row-1 && check.field===field)?.cell;
          if (ref) cell.conditionalFormats.addCustom(`=${ref}=${literal(record.values[field])}`,{fill:pink,font:{color:'#9C2721'}});
        } else cell.format.fill=yellow;
      }
    }
  }
  sheet.getRangeByIndexes(1,0,matrix.length,headers.length).format.rowHeight=52;
  const notes=sheet.getRangeByIndexes(1,headers.indexOf('备注'),matrix.length,1);
  notes.format.wrapText=true;
  sheet.getRangeByIndexes(1,headers.indexOf('具体地点'),matrix.length,1).format.wrapText=true;
  notes.format.autofitRows();
  workbook.recalculate();
  await preview(workbook,data.sheet,`main-${part.index}-collection`,'U1:AG6');
  await preview(workbook,data.sheet,`main-${part.index}-storage`,'AO1:AT5');
  await preview(workbook,data.sheet,`main-${part.index}-notes`,'BL1:BL5');
  await fs.mkdir(path.dirname(mainPath(part.index)),{recursive:true});
  await (await SpreadsheetFile.exportXlsx(workbook)).save(mainPath(part.index));
  evidence.filledSha256 = sha256(await fs.readFile(destination));
  await fs.writeFile(path.join(previewDirectory,'template-copy-evidence.json'),JSON.stringify(copyEvidence,null,2));
}

const review = Workbook.create();
const recordsSheet = review.worksheets.add('逐条核对');
const validationSheet = review.worksheets.add('模板校验');
function setup(sheet,title,headers,widths,rows) {
  sheet.showGridLines=false;
  sheet.getRangeByIndexes(0,0,Math.max(7,rows.length+6),headers.length).format.font={name:'Arial',size:10,color:'#222222'};
  sheet.getRange('A2').values=[[title]];
  sheet.getRange('A2').format.font={name:'Arial',size:14,bold:true};
  sheet.getRangeByIndexes(5,0,1,headers.length).values=[headers];
  const heading=sheet.getRangeByIndexes(5,0,1,headers.length);
  heading.format={fill:'#304C63',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},rowHeight:28,verticalAlignment:'center'};
  heading.format.horizontalAlignment='center';
  widths.forEach((width,col) => sheet.getRangeByIndexes(0,col,Math.max(7,rows.length+6),1).format.columnWidth=width);
  if (rows.length) {
    sheet.getRangeByIndexes(6,0,rows.length,headers.length).values=rows.map(row => row.map(safeText));
    sheet.getRangeByIndexes(6,0,rows.length,headers.length).format.verticalAlignment='center';
    const lastColumn=String.fromCharCode(64+headers.length);
    sheet.tables.add(`A6:${lastColumn}${rows.length+6}`,true,sheet.name==='逐条核对'?'SpecimenReview':sheet.name==='标签纠错'?'LabelCorrections':'TemplateReview');
    sheet.getRangeByIndexes(6,0,rows.length,headers.length).format.fill='#FFFFFF';
  }
  sheet.freezePanes.freezeRows(6);
}
const rows=data.audit.map(record => {
  const v=record.values;
  const gridSources=record.grid.candidates.flatMap(c => c.sources.map(s => `${c.id}：${s.sheet} 第${s.row}行`)).join('；');
  return [data.sources[record.source].name,record.page,record.part,record.excel_row,record.specimen,v['区县'],v['具体地点'],
    v['采集日期'] ? new Date(`${v['采集日期']}T00:00:00Z`) : null,v['采集方式'],v['网格号'],record.grid.edge_distance_m,
    record.identification_label_present?'有':record.identification_evidence?'用户确认':'未见',record.issues.join('；'),null,null,gridSources];
});
setup(recordsSheet,'标本标签人工核对',
  ['来源文件','来源页/图','输出分册','Excel行','标本号','区县','具体地点','采集日期','采集方式','网格号','距边界(m)','鉴定标签','需核对事项','人工核对结果','人工备注','网格来源'],
  [32,12,11,10,23,20,42,15,13,11,17,15,85,18,45,80],rows);
const near=data.audit.filter(r => r.flags.some(f => f.kind==='near_edge')).length;
const missing=data.audit.filter(r => r.flags.some(f => f.kind==='identification')).length;
const unmatched=data.audit.filter(r => r.grid.status!=='matched').length;
const summary=data.validation_summary;
recordsSheet.getRange('A3').values=[[`${data.record_count}条；近边界提示${near}条；缺鉴定标签${missing}条；网格留空${unmatched}条；模板冲突${summary.conflict}处。`]];
recordsSheet.getRange('A4').values=[['近边界阈值为复核提示；字段口径见随附说明。核对清单为生成时快照，修改主表后需重新校验。']];
recordsSheet.getRange('A5').values=[[data.parts.map(p => `分册${p.index}：${path.basename(mainPath(p.index))}`).join('；')]];
recordsSheet.freezePanes.freezeColumns(5);
recordsSheet.getRangeByIndexes(6,0,rows.length,16).format.rowHeight=58;
recordsSheet.getRangeByIndexes(6,0,rows.length,1).format.wrapText=true;
recordsSheet.getRangeByIndexes(6,1,rows.length,3).format.horizontalAlignment='center';
recordsSheet.getRangeByIndexes(6,8,rows.length,1).format.horizontalAlignment='center';
recordsSheet.getRangeByIndexes(6,11,rows.length,1).format.horizontalAlignment='center';
recordsSheet.getRangeByIndexes(6,7,rows.length,1).setNumberFormat('yyyy-mm-dd');
recordsSheet.getRangeByIndexes(6,10,rows.length,1).setNumberFormat('0.00');
recordsSheet.getRangeByIndexes(6,12,rows.length,1).format.wrapText=true;
recordsSheet.getRangeByIndexes(6,15,rows.length,1).format.wrapText=true;
recordsSheet.getRangeByIndexes(6,14,rows.length,1).format.wrapText=true;
recordsSheet.getRangeByIndexes(6,13,rows.length,2).format.fill=yellow;
recordsSheet.getRangeByIndexes(6,13,rows.length,1).dataValidation={rule:{type:'list',values:['已核对','需修正']}};
recordsSheet.getRangeByIndexes(6,13,rows.length,1).conditionalFormats.add('containsText',{text:'需修正',format:{fill:pink}});
data.audit.forEach((record,index) => {
  if (record.flags.some(f => ['grid','near_edge'].includes(f.kind))) recordsSheet.getRangeByIndexes(index+6,9,1,2).format.fill=yellow;
  if (record.flags.some(f => f.kind==='identification')) recordsSheet.getRangeByIndexes(index+6,11,1,1).format.fill=yellow;
});
const statusLabels={conflict:'用户规则与模板冲突',invalid:'不符合模板',unsupported:'尚未自动验证'};
const problems=data.validation_checks.filter(check => check.status!=='passed');
const validationRows=problems.map(check => {
  const record=data.audit[check.record_index];
  return [check.part,check.cell,check.field,String(check.value),statusLabels[check.status],check.expected,check.specimen,data.sources[record.source].name,record.page,null];
});
setup(validationSheet,'模板合法值检查',
  ['输出分册','单元格','字段','实际值','结果','模板要求','标本号','来源文件','来源页/图','人工处理说明'],
  [12,13,18,22,28,80,23,20,12,45],validationRows);
validationSheet.getRange('A3').values=[[`已检查${summary.checked}个已填单元格：通过${summary.passed}，规则冲突${summary.conflict}，其他异常${summary.invalid}，未支持${summary.unsupported}。`]];
validationSheet.getRange('A4').values=[['保留用户值；冲突未解决前不能宣称全部模板验证通过。列表比较去掉选项首尾空格，保留编号。']];
if (validationRows.length) {
  validationSheet.getRangeByIndexes(6,0,validationRows.length,10).format.rowHeight=32;
  validationSheet.getRangeByIndexes(6,0,validationRows.length,1).format.horizontalAlignment='center';
  validationSheet.getRangeByIndexes(6,5,validationRows.length,1).format.wrapText=true;
  validationSheet.getRangeByIndexes(6,4,validationRows.length,1).format.fill=pink;
  validationSheet.getRangeByIndexes(6,9,validationRows.length,1).format.fill=yellow;
}
const correctionData=data.label_correction_review;
if (correctionData?.rows?.length) {
  const corrections=review.worksheets.add('标签纠错');
  setup(corrections,'模糊与破损标签纠错',correctionData.headers,correctionData.widths,correctionData.rows);
  const c=data.label_correction_summary;
  corrections.getRange('A3').values=[[`跨标签补全${c.applied}处；证据不足${c.pending}处；参考冲突${c.ambiguous}处；按规则处理${c.rule_managed}处。全部待人工核对。`]];
  corrections.getRange('A4').values=[['补全值来自独立清晰标签；参考值冲突时留空。补全不等于人工已确认。']];
  corrections.getRangeByIndexes(6,0,correctionData.rows.length,correctionData.headers.length).format.rowHeight=110;
  corrections.getRangeByIndexes(6,0,correctionData.rows.length,correctionData.headers.length).format.wrapText=true;
  corrections.getRangeByIndexes(6,7,correctionData.rows.length,2).setNumberFormat('@');
  correctionData.rows.forEach((row,index) => {
    if (['经度','纬度'].includes(row[5])) corrections.getRangeByIndexes(index+6,7,1,2).setNumberFormat('0.00000000');
  });
  corrections.getRangeByIndexes(6,8,correctionData.rows.length,1).format.fill=yellow;
  corrections.getRangeByIndexes(6,13,correctionData.rows.length,2).format.fill=yellow;
  corrections.getRangeByIndexes(6,13,correctionData.rows.length,1).dataValidation={rule:{type:'list',values:['已核对','需修正']}};
}
review.recalculate();
await preview(review,'逐条核对','review-source','A1:F10');
await preview(review,'逐条核对','review-issues','H6:O10');
await preview(review,'模板校验','validation','A1:F11');
if (correctionData?.rows?.length) await preview(review,'标签纠错','label-corrections','E6:M9');
await fs.mkdir(path.dirname(reviewOutput),{recursive:true});
await (await SpreadsheetFile.exportXlsx(review)).save(reviewOutput);
if (sha256(await fs.readFile(template)) !== templateSha256) throw new Error('Original template changed');
console.log(JSON.stringify({main:destinations.slice(0,-1),review:reviewOutput,records:data.record_count,validation:summary,templateCopiedBeforeFilling:true,templateSha256}));
