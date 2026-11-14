# 模糊与破损标签纠错

先识别质量问题，再对照独立清晰标签补全同一采集事件的共享字段。每个补全值都与原始读数分开保存。脚本核验结构化证据，不负责替模型判断图片是否清晰，也不会修改原图或生成缺失文字。

## 看图与标注

逐张放大采集、编号和鉴定标签。反光、失焦、小字压缩、墨迹缺失、撕裂或遮挡影响读数时，分别标记 `unclear`、`damaged`、`missing`；确实能读清则标记 `clear`。在 `issues` 说明受影响区域和原因。保留可见原文及 OCR 候选，完全读不到的原文为 `null`。

`label_quality` 按 `collection`、`catalogue`、`identification`、`scale` 标记整张标签。整张标签确认清晰时，可以只标该角色 `clear`，程序将其实际转录的对应字段视为清晰候选。标签整体模糊或破损时，仍读清的字段应在 `field_quality` 中明确标为 `clear`；未单独说明的已有读数按该标签的质量处理。

局部质量优先于整张标签标注。只有独立看过的原始清晰字段能充当参考；`visually_checked: true` 本身不表示所有字段都清晰。真正查看目标和参考标签、核对对应关系及目标仍能读清的局部文字后，把目标记录的 `cross_label_reviewed` 置为 `true`；这是模型的比对记录，不是用户的人工确认。剩余可读文字与参考矛盾时保留待核对，不能因其他字段相同而覆盖；参考转录改变后应重新查看比对。

## 自动匹配依据

程序要求至少一组原始清晰锚点完全一致：

- 具体地点 + 采集日期；
- 经度 + 纬度 + 采集日期。

还会检查双方其他原始清晰采集字段；已读清内容有冲突时排除该参考。同一张合照、同一省县、编号相邻、文字布局相似或多个 OCR 给出相同答案，都不能单独证明属于同一采集事件。

地点使用 Unicode 标准化和空白整理；数字按原数值相等判断，不使用地理近似或模糊相似度代替相同事件。日期必须为有效的 `YYYY-MM-DD`，经纬度和海拔须为数值。

仅补全共享采集字段：国家、省、地级市、区县、具体地点、经度、纬度、海拔、采集日期、采集人。馆藏号、独立鉴定、性别、数量等不能从别的个体套用。已有 `identification_evidence` 记录用户明确确认的鉴定填写值时，保留该值并注明来源为用户确认；不把它算成跨标签补全。固定/派生字段按本批规则处理；“采集方式”仍按该枚原标签与已确认规则判定，不因为参考标本灯诱而改成灯诱。比例尺有缺损时只标记复核，不补画或借用别的照片的尺。

一个独立清晰参考即可提供候选；有多个合格参考时必须全部一致。任何清晰参考冲突都保持留空，不按多数票选择。已补全字段永远不作为新的清晰来源，也不成为下一条记录的匹配锚点。

## 记录格式

以下只演示结构，不能当作真实观测：

```json
{
  "record_id": "target",
  "source": "photo1",
  "page": 1,
  "visually_checked": true,
  "cross_label_reviewed": true,
  "identification_label_present": false,
  "collection_label_text": "示例采集点A，2026-06-28，经度114.28??，海拔破损",
  "label_quality": {"collection": "damaged"},
  "field_quality": {
    "国家": "clear", "省": "clear", "具体地点": "clear", "采集日期": "clear",
    "纬度": "clear", "经度": "unclear", "海拔": "damaged"
  },
  "values": {
    "标本号": "DEMO-002", "国家": "中国", "省": "河北省", "具体地点": "示例采集点A",
    "采集日期": "2026-06-28", "经度": "114.28??", "纬度": 38.22001159, "海拔": null
  }
}
```

参考记录应保留自己的来源、编号、原文和已看图标记；使用 `label_quality.collection: clear` 或逐字段 `clear` 注明真实清晰信息。`record_id` 在本批唯一；合照桥接器默认使用馆藏号，PDF 编译器可生成来源/页/组索引。

## 日期或整张标签完全缺失时

无法建立锚点时保持待核对。若用户明确确认某几条共享同一采集事件，可在批次顶层提供：

```json
{
  "shared_collection_groups": [{
    "id": "event-one",
    "record_ids": ["target", "reference"],
    "fields": ["采集日期", "经度", "海拔"],
    "evidence": {"type": "user_confirmation", "statement": "用户明确确认共享采集事件的原话"}
  }]
}
```

这个分支允许在相应字段缺少锚点时核对参考值，仍检查已读清内容是否矛盾。仅在用户确实确认时填这个证据；“请纠错”不等于确认所有标本同地同日，也不能把模型判断写成用户原话。

## 运行与交付

`specimen_data.py` 在固定规则和网格计算前自动执行纠错。有需要时可先独立检查：

```text
python scripts/run.py label_corrections.py RECORDS.json --out CORRECTED_RECORDS.json --report CORRECTIONS.json
python scripts/run.py specimen_data.py CORRECTED_RECORDS.json --out COMPILED.json
```

输入和原文保持不变。输出的 `observed_values` 保存原始读数，`values` 只在有证据时补全；未解决的非清晰字段置空。`label_corrections` 逐字段保存原值、结果、参考来源、字段值、图像指纹和匹配依据。重复执行从原始读数重新核验，不把上次补全当成新观察。

编译结果包含 `label_correction_summary` 和 `label_correction_review`；写表后主表补全字段有提示色、备注有来源，核对工作簿增加“标签纠错”页。人工核对结果与备注初始空白。`verify_workbooks.py` 读回该页逐格核对原始读数、填写值和来源；坐标补全后网格按补全坐标重新计算。

输出统计分别列跨标签补全、证据不足、参考冲突和按规则处理。保留待核对状态，不把补全结果称为原标签直接读出的文字，也不把脚本测试称为真实模糊图片识别准确率。
