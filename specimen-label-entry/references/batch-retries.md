# 批次整理、回原图复核与直接截取

多照片批次，或出现未读字段、漏组、几乎空白的切图、反复压叠时使用。按实际馆藏号建清单，保留每轮输入、观察和输出；数量不固定。示例号段、日期、名称与坐标仅说明格式，不是新批次的事实。

## 十步流程

| 步骤 | 操作 | 完成依据 |
|---|---|---|
| 1 | EXIF 时间和已读号段辅助归组 | 每张原图有唯一来源键；空桌面、容器和重拍分别记录 |
| 2 | 定框切组 | 原图坐标正确；密集照片可用 `--padding 0`；逐张看实际切图 |
| 3 | 逐组 OCR 与视觉转录 | 编号、实体及其采集/鉴定标签配对正确 |
| 4 | 只写直接读到的值 | 未读值留空，原 OCR 另存，逐字段标注质量 |
| 5 | 跨标签纠错 | 按清晰地点+日期或坐标+日期核验同采集事件，保留参考证据 |
| 6 | 回原图扩裁、放大一次 | 仍模糊的采集卡外扩 60 个归一化单位，另生成 2× 预览；检查最后一行 |
| 7 | 直接看原片 | 仍未读出或整组缺失时看整张、左右半张或上下条带，不再循环挪框 |
| 8 | 必要时直接截取 | 密集布局反复碰撞时，不再要求组件框；按原片位置截取完整一组并保存来源 |
| 9 | 整理交付与留空留痕 | 重拍逐号选图去重；输出规范文件名、纠错页与不清晰明细 |
| 10 | 分别验表、验图和验覆盖 | 主表读回一致；图片来源及像素可复现；未解决项仍有定位 |

正式交付照片还须执行 [完整性与单尺验收](photo-completeness.md)：编号纸、标本及附肢、采集标签整纸、恰好一把完整比例尺，逐张看生成结果并绑定哈希。EXIF/阅读转向变化后重建裁框；相邻框仅接触边缘不必留间隙，真实重叠也不能通过缩小目标解决。

标准切图后若已经清楚，直接进入后续步骤。能清晰读出的区间值不必反复 OCR，应按区间规则留空。放大和原片复核后重新保存转录、编译及核对清单；旧编译结果不是新的原始观察。

## 1. 来源清单、候选编号与重拍

```text
python scripts/photo_review.py inventory PHOTO1.JPG PHOTO2.JPG --ocr WORK/ocr-all.json --number-prefix 2833 --out WORK/photo-inventory.json
```

`--ocr` 可省略；提供时采用随附 macOS OCR 的 `[{"image":"...","lines":[{"text":"..."}]}]` 结构。清单记录源哈希、EXIF 时间、转正后的宽高与 OCR 候选。默认提取独立的 7 位数字，`--digits` 可改；`--number-prefix 2833` 只适用于已确认使用这一号段的批次，其他批次按实际设置或省略。

OCR 可能把 `IOZ` 读成 `TOZ`、`10Z` 或 `1OZ`，候选提取应能容纳这些前缀错误。候选仍需回原片读完整馆藏号；不能把数字里的 `O` 猜成 `0`，也不能从连续号段生成缺号。号段中的两端不证明中间每枚都已拍到。清单保留零 OCR 的照片和区间/列表提示，不能据此自动排除照片或生成记录。

EXIF 时间只辅助照片排序与批次分组，不是采集日期。同一张照片可能混有不同产地或日期。自适应找卡、编号卡条带 OCR、行列布局均可产生候选位置；固定偏移和行列插值不能替代读编号和看归属。候选数量少于清单时转到原片复核。

同一馆藏号重拍时逐枚选择信息完整、配对清楚的一份，保留其他来源备查。不要整张只选最新照片：某个编号可能只在另一张中出现。同号但信息冲突时先列问题，不拼成一条看似完整的记录。合并 JSON 时为各照片分配唯一 `source` 键，同时更新记录引用；标本号保留完整馆藏号，不能拿文件名代替。

## 2–5. 首轮切图与证据填写

执行 [合照流程](multi-specimen-photos.md)；密集照片先减少外框空白而不截断实体：

```text
python scripts/photo_groups.py split PHOTO.JPG WORK/plan.json --padding 0 --out WORK/split-01
```

`specimen` 是形态，`catalogue` 是馆藏号标签，`collection` 是采集卡。首轮切图过薄、贴边或几乎空白时，记录当前证据图质量和框的问题。当前切图中采集标签缺失可记 `label_quality.collection: missing`，但这不能证明原片没有标签。回原片复核后再更新质量与直接读数。

`values` 只写直接读到的单值；可疑 OCR 放 `ocr_text` 或 `observed_values`，未读字段标 `field_quality: unclear`。按照 [标签纠错](label-corrections.md) 调用编译器。共同出现在一张照片中不等于同一采集事件；不能复制同片整套文字到每枚，也不能制造 `user_confirmation` 让程序通过。馆藏号、鉴定、性别和数量不跨个体补全；清晰参考冲突时留空，不投票。

## 6. 回原图扩裁与 2× 复核

首轮纠错后仍有未读字段时，从原图重新裁采集卡，而不是放大旧的残缺切片：

```text
python scripts/photo_review.py cards WORK/split-01/groups-manifest.json --specimens "IOZ(E)0000001" --padding 60 --factor 2 --out WORK/review-expanded
```

这里的 60 是整图的 `normalized_0_1000` 单位：横向外扩 `W×60/1000` 像素，纵向外扩 `H×60/1000` 像素，裁框限制在原片内。脚本保存原尺寸 `__native.png` 和另存的 `__2x.png`，记录哈希、原框、扩裁框和倍数。缺少采集卡框时可显式增加 `--region group` 扩裁整个组图，或转看原片；不能把形态框冒充采集卡位置。

先看原尺寸和 2× 图，再视环境跑 OCR。重点确认最后一行的采集人、海拔和日期是否被首轮截掉；同时避免把外扩进来的邻组文字归给本组。2× 图只供 OCR/阅读，不能作为原像素交付图，也不能与未缩放比例尺混合测量。

每次重试在记录的 `review_attempts` 保存真实经历，例如：

```json
{
  "stage": "expanded_card",
  "review_manifest": "/absolute/WORK/review-expanded/review-manifest.json",
  "fields": ["采集人", "海拔"],
  "outcome": "原图最后一行可读，已直接转录"
}
```

重新读到的值在新版转录中记为自己的直接观察，修正 `field_quality`；保留先前 OCR 和旧切图在历史中。不要把上轮补全值升级成独立清晰参考。若原先的“区间”经原片证明确为 OCR 误读，可在新版转录中修正对应 `interval_observations` 并说明依据；真实区间仍留空。

## 7. 看整张、半张或条带

扩裁后仍失败，或者整组漏掉时，切换观察方式。不要仅反复换组件坐标后宣布原片被截断：

```text
python scripts/photo_review.py source PHOTO.JPG --layout halves --out WORK/source-halves
python scripts/photo_review.py source PHOTO.JPG --layout bands --segments 3 --out WORK/source-bands
```

`--layout whole` 输出整张；`halves` 按左右分区，`bands` 按上下分区。输出不画框，保留原尺寸源区域和覆盖记录。先用一种足够看清的分区，必要时再换一种，不必机械执行所有布局。用实际编号找到实体与全部标签，回看交界处；不能由预期行列位置推出编号。

标签在原片中倒置时，`source` 或 `cards` 可加 `--rotation 180`；也支持逆时针 90/270 度。转向只作用于另存的阅读副本，原尺寸证据图与原片坐标不变。手工截取计划仍使用 EXIF 转正原片的坐标，不能直接抄旋转预览的位置。

原片阅读仍失败才区分真实模糊/破损、标签确实缺失、原片画幅之外、以及尚未定位。原图中没有可读编号时，保存待核对区域与来源，不能用连续号填空。整组暂未完成时保留在覆盖清单中，不因一次重建失败而把已确认的馆藏号静默删掉。

## 8. 不再画组件框的直接截取

同一布局反复因行距密集、外框碰撞失败时，使用源图中实际看到的范围直接裁一组；无需继续标出三个角色框。计划保留源哈希、原尺寸、已看清的完整馆藏号和来源外框：

```json
{
  "source_sha256": "从 photo-source.json 复制",
  "display_size": [6000, 4000],
  "coordinate_space": "normalized_0_1000",
  "direct_review": {
    "visually_checked": true,
    "reason": "已看原片确认归属，组件框反复压叠；按实际位置直接截取"
  },
  "groups": [{
    "specimen_number": "IOZ(E)0000001",
    "id_verified": true,
    "pairing_reviewed": true,
    "identification_label_present": false,
    "group_box": [100, 200, 450, 600],
    "issues": ["按原片位置直接截取；保留实际来源与待核对事项"]
  }]
}
```

```text
python scripts/photo_groups.py direct PHOTO.JPG WORK/direct-plan.json --out WORK/direct-groups
python scripts/photo_groups.py verify WORK/direct-groups/groups-manifest.json
```

示例框需替换为实际位置。`visually_checked` 表示执行者已看图，不代表用户已人工核对。直接模式保留外框重叠警告，仍要求编号复核、来源哈希、边界和像素校验；纵坐标按图片高度换算。输出为不画框的 PNG，不能只留编号条而声称形态、采集卡和比例尺都完整。去邻组残片时使用 [精细清理副本](precision-cleanup.md)，保留原始证据。

直接组图可以没有 `components`。此时原片复核仍从整图/条带进行；如需叠共享尺，按 [共享尺流程](multi-specimen-photos.md) 声明同片的 `shared_scale`，用 `shared_scale.py add ... --targets group`。整张直接组图作为保护区，尺置于扩展背景，或保留图中已有原尺。没有独立形态框时不生成假形态小图。

用 `photo_records.py` 将直接分组与转录连接到原流程。采集信息仍按该枚直接读数或已核验的跨标签证据填写；直接截取不是整张套值的依据。

## 9–10. 命名、留空、交付与验证

内部证据图可继续使用完整馆藏号；交付图使用去掉 `IOZ(E)`、加 `-1` 的命名，例如 `图像/0000001-1.png`。表格“标本号”仍为 `IOZ(E)0000001`。先合并各照片转录并逐号解决重拍重复，再按最终记录复制图片：

```text
python scripts/photo_delivery.py export WORK/records-merged.json --out WORK/delivery/图像 --records-out WORK/delivery/records.json --compositions WORK/complete-01/complete-manifest.json --require-complete
python scripts/photo_delivery.py verify WORK/delivery/图像/delivery-manifest.json
```

有带尺/清理后合成图时，`export` 增加 `--compositions WORK/带比例尺分组/compositions-manifest.json`；多照片可传多个合成清单。脚本校验原始组图的来源框和像素、合成证据，逐字节复制图片，更新新记录中的相对“图像”路径，保留馆藏号与其余数据。同号或去前缀后的文件名冲突时停止，不覆盖。放大预览和画框诊断图不能冒充原像素证据。主表应放在 `WORK/delivery/`，与 `图像/` 的相对关系一致。

上例采用新的单尺验收清单。旧的 `groups-manifest.json` / `compositions-manifest.json` 仍能校验原始裁切与叠尺，但不能代替完整验收；`--require-complete` 会拒绝仅有旧证据的记录。

`1600 m - 1800 m`、`2026-07-01 至 07-02` 等属于区间；编译器保留原文并将单值列留空，不取端点、均值或其他标签的单值。可用 `interval_observations: {"海拔": {"raw_text": "1600–1800 m"}}` 明确标记；编译器也识别原文中的典型海拔/日期区间。区间不能充当其他记录的清晰单值参考。

```text
python scripts/specimen_data.py WORK/delivery/records.json --out WORK/compiled-final.json
python scripts/write_review.py WORK/compiled-final.json --out WORK/delivery/核对说明.md --unclear-out WORK/delivery/标签不清晰明细.md
```

继续按入口先完整复制参考 Excel，再填副本，读回验证全部分册和核对表。纠错页允许真实空单元格与空字符串等价，仍逐项核对有值的参考、空格、零值与公式。区间、未解决字段和补全证据进入“标签纠错”页与按照片排列的明细。

收尾分别确认：已读馆藏号无重复、每条正式记录恰有一张所选交付图、每张图仍可追溯、照片均有记录或明确排除/待办理由。记录数相等、图片数相等和 Excel 逐格校验通过，只证明这些检查自身；还需实际看图确认形态与标签完整、配对正确。号段缺口保留“本批未拍到/待定位”的证据，不补造记录。
