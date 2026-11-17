# 多枚标本合照：先分组，再录入

适用于 JPG/PNG 一图多枚的输入。目标是每个可确认的馆藏号一张完整证据图，另附形态、编号、采集、鉴定、比例尺局部图，随后接入原 Excel 流程。模型负责对应关系和看图读字；Python 负责坐标、文件、原像素裁切与一致性校验。GPT-6 Luna 可按下列小步骤逐项完成，每一步检查通过再继续。

## 准备原图与坐标

多照片先按 [批次与重试流程](batch-retries.md) 建来源/重拍清单；未读字段和漏组按“回原图扩裁 → 直接看原片 → 必要时直接截取”复核。OCR 没读出编号或切片空白，不能据此断言原图无标本。

使用含 Pillow、NumPy 的 Python。安装后通过 `python scripts/run.py HELPER.py ...` 调用安装器选择的解释器；Codex 也可通过 `load_workspace_dependencies` 定位内置运行环境。其他宿主见 [宿主适配](host-adaptation.md)。路径相对本 skill；WORK 为本批工作目录，准备和裁切分别选新目录。

```text
python scripts/photo_groups.py prepare PHOTO.jpg --work WORK/prepared
```

查看 `overview.jpg` 和 `overview-grid.jpg`，需要时放大原图。所有框使用 EXIF 转正后的整张图，`[左,上,右,下]`，归一化到 0–1000；宽高和 SHA-256 从 `photo-source.json` 读取。不要使用预览像素作为原图坐标。

先逐个读出完整馆藏号并找到其标本、标签、比例尺。单枚、数枚、大量标本均适用，不预设固定数量或布局。先扫描全图建立实际编号清单，数量多时按可读区域分批放大，再合并、去重并回看交界处；局部坐标统一换算回整张原图。`expected_groups` 可省略，或填写本张实际确认的数量作为漏组检查；这个字段不设数量上限，也不能从示例照抄。按实际摆放关联，不能用等分图块或编号连续性替代看图配对。无法对应的组保留待核对，不能丢弃后宣称全部识别。编号数表示标本个体数，不能据此推断物种数。

## 写分组计划并裁切

`plan.json` 的最小格式如下。示例编号和坐标仅演示结构，实际必须看图确定。

```json
{
  "source_sha256": "从 photo-source.json 复制",
  "display_size": [6000, 4000],
  "coordinate_space": "normalized_0_1000",
  "expected_groups": 1,
  "groups": [{
    "specimen_number": "IOZ(E)0000001",
    "id_verified": true,
    "pairing_reviewed": false,
    "identification_label_present": false,
    "components": {
      "specimen": [400, 300, 600, 600],
      "catalogue": [400, 650, 650, 740],
      "collection": [50, 100, 350, 600],
      "identification": null,
      "scale": [420, 100, 620, 250]
    },
    "issues": []
  }]
}
```

每个角色是包住全部内容的矩形，含触角、足及标签边缘；缺失的角色为 `null`。`specimen` 和 `catalogue` 必填。若有多张同类标签，把它们的并集框作为该角色，完整组图保留原有位置。可提供 `group_box` 明确外框；否则程序取所有角色的并集并加少量边距。过度宽大的框可能碰到邻组，应收紧空白或用明确外框，不要靠裁去真实部件来通过校验。

```text
python scripts/photo_groups.py split PHOTO.jpg WORK/plan.json --out WORK/split-01
```

输出馆藏号命名的 PNG、`components/`、`groups-manifest.json` 和分组预览。原图保持原文件，证据图只做 EXIF 转正与整像素矩形裁切，不缩放、不调色。跨组相交检查只针对已声明的框，不能替代视觉复核。

逐张查看完整组图、形态局部图和编号局部图，对照原图确认：

- 编号和实体、采集标签、鉴定标签、比例尺属于同组；邻组内容未混入。
- 形态局部图完整保留头部、细触角、足和翅缘，实体四周留出可见余量。任一部位贴住图框时，回看原图并检查该边；不能只检查顶部触角。缩略全图估计的框常会截断实体，必须看裁后局部图。
- 所有标签可读，图中未见鉴定标签时，鉴定字段为空。

若有截断或错配，修改计划、导出到新目录并重看受影响的图。全部看过后，在计划中把 `pairing_reviewed` 置为 `true`，再次导出最终版本并检查。

```text
python scripts/photo_groups.py verify WORK/split-final/groups-manifest.json
```

这个验证证明源文件未变、所有裁切的解码像素与源图对应区域一致；**不证明框中的内容配对正确或边缘完整**，后两项由看图记录支撑。`human_review_pending` 始终保持为真，人工核对留到用户执行。

密集布局可用 `--padding 0`；反复因外框压叠失败时，按 [直接截取](batch-retries.md) 使用 `photo_groups.py direct`，无需继续画组件框。此模式仍保留来源、像素和重叠核验；不会把不完整编号条当成完整一组。

裁切中出现标签残字、黑框或邻组碎片时，按 [精细裁边](precision-cleanup.md) 生成原尺寸坐标图，重新选框或使用经看图确认的排除区与形态保护区。完整组图应保留本组标签；形态图可在另存副本中清理非形态内容。跨过标签边框的触角保持完整，交叠像素保留并标注。

## 一张原图只有一个共享比例尺

先放大比例尺，确认裁切框完整包含刻度线、端点、数值和单位。这个尺属于原图，不属于某一枚标本；只在计划顶层增加如下字段（坐标为结构示例，实际需看图），各组 `components.scale` 均设为 `null`，避免把远处的尺纳入每个原始裁切框：

```json
{
  "shared_scale": {
    "box": [30, 850, 240, 980],
    "label_text": "10 mm",
    "visually_checked": true
  }
}
```

同一次 `split` 会从原图裁出唯一的 `shared-scale.png`，并在清单中保存其源坐标及原文。尺框不得混入其他标本或标签；尺的数字、单位看不清时先标待核对，不能猜数值。

完成逐组视觉检查、最终原像素裁切后，生成带尺成品：

```text
python scripts/shared_scale.py add WORK/split-final/groups-manifest.json --out WORK/带比例尺分组
python scripts/shared_scale.py verify WORK/带比例尺分组/compositions-manifest.json
```

默认每个实际馆藏号产生两张带尺图：目录根部为完整组图，`specimens/` 为单独形态图。因此 N 枚应得到 N 张完整组图和 N 张形态图。编号、采集等仅供读字的局部图继续作为原始证据，不加尺。

默认 `--style overlay`：程序从原尺提取深色刻度、数字和单位，去除纸片底色，输出 `scale-cutout.png`。这是源像素的透明度蒙版，不重画刻度或文字；紧贴墨迹裁边不会改变刻度间的像素距离。先查看透明尺，确认数值、单位及全部端点仍清楚。背景与墨迹分不开时重新选框或转人工，不能生成替代尺。

使用 `shared_scale.py extract ... --out WORK/scale-preview` 先查看白底和深底预览，透明图的隐藏 RGB 不能作为残留判断依据。仍有异物或纸边时，按 [比例尺精细蒙版](precision-cleanup.md) 写原尺寸的保留/排除多边形，再用 `--scale-mask` 提取与合成。裁边清理结果通过 `--cleanup` 接入；验证会同时重建清理副本和叠尺结果。

透明尺优先放在完整组图内的安全空白处，避开实体、触角、足和所有标签框；空白不足时扩展与原图接近的纸张底色。默认无白底页脚、无英文说明和分隔线。扩展区域仅为合成背景，原始裁切不变。完整组图若已完整包含原尺，保留该尺而不重复添加。单独形态图使用同样规则。

标本、原标签和比例尺几何尺寸不重新采样或旋转；尺宽于可用空间时扩大画布，不能缩短尺来迁就版面。蒙版会改变尺边缘与背景的混合像素，因此不能把合成图称为整幅原像素裁切。验证会单独检查源图及原始裁切、透明尺的源框与尺寸、标本和标签保护区的像素，并复现全部合成图。旧版白底页脚可用 `--style footer` 显式选择，旧清单仍可验证。

共享尺复制仅适用于同一张来源照片。它提供同图参考，不自动证明真实物理尺寸已经校准；检查拍摄是否基本垂直、标本与尺是否同一平面、画面各处是否有明显透视变化。存在这些问题时保留 `measurement_status` 的待核对状态，不进行无依据的尺寸测量或透视修正。后续若缩放整张成品，标本与尺必须一起等比例缩放。

## 每组转录后进入原流程

逐组识别时先检查标签清晰度。模糊、反光、缺字、撕裂或缺失影响字段时，执行 [标签纠错](label-corrections.md)：标注原始读数与质量，对照同一采集事件的独立清晰标签，补全后保留来源。不要在裁切图上生成缺字，也不要把其他个体的馆藏号或鉴定信息复制过来。桥接器保留质量标注与用户明确确认的共享采集组；纠错在编译时执行，补全坐标后重新匹配网格。

写 `transcription.json`，每个组恰有一条记录，完整格式参照 [记录约定](record-format.md)。各组单独读标签；文字相同也保存各自原文。

```json
{
  "sheet": "biaobenku",
  "records": [{
    "specimen_number": "IOZ(E)0000001",
    "visually_checked": true,
    "identification_label_present": false,
    "identification_label_text": null,
    "collection_label_text": "此组采集标签原文",
    "values": {"国家": "中国", "经度": 118.1, "纬度": 40.1},
    "issues": []
  }]
}
```

```text
python scripts/photo_records.py WORK/split-final/groups-manifest.json WORK/transcription.json --out WORK/records.json
python scripts/specimen_data.py WORK/records.json --out WORK/compiled.json
```

桥接器检查所有馆藏号各覆盖一次、图组配对已检查、鉴定标签状态一致，并写入原图指纹和框、局部图路径。`图像` 默认填 `原像素分组/馆藏号.png`；交付时建立同名相对目录。主表备注标明原图与馆藏号，便于追溯。

上述桥接默认路径保留兼容；正式照片交付按批次流程再运行 `photo_delivery.py`，复制为 `图像/去前缀馆藏号-1.png` 并生成更新后的记录，表格标本号仍为完整馆藏号。共享尺和清理来源继续保留。

使用共享尺时改用下列桥接命令，使 Excel 的“图像”指向带尺完整组图，同时在结构化记录保留未修改证据图和共享尺的来源。交付时将目录命名为 `带比例尺分组` 并与 Excel 放在一起。

```text
python scripts/photo_records.py WORK/split-final/groups-manifest.json WORK/transcription.json --compositions WORK/带比例尺分组/compositions-manifest.json --out WORK/records.json
```

继续执行入口的写表、读回验证和说明生成。主表必须先完整复制参考 Excel，再在副本上填写，使用更新后的 `build_workbook.mjs`，检查 `template-copy-evidence.json` 中填入前的副本指纹一致。交付完整组图、Excel 主表、人工核对表、说明及可再运行的 skill。宣称 Luna 实测时注明实际模型、样本数、是否重裁和是否有人修正；一张图通过不能代替后续批次检验。

## 可选抠图与调色

使用用户授权的本地图片处理工具，从完整形态局部图另存展示副本，和原像素证据图分目录。本地保守处理示例（需 Pillow、NumPy）：

```text
python scripts/presentation_copy.py WORK/split-final/groups-manifest.json --out WORK/presentation --cutout
```

脚本估计纸张背景色，有限度中和颜色，再产生带透明度的背景移除草稿；检查触角、足及半透明翅缘，灰尘或阴影可能保留。缺少色卡时只能称为估计白平衡，不能称为真实色彩还原。证据识别、定种、形态测量和 Excel 图像引用始终使用未调色原像素图。抠图失败则交付原图并具体说明，不生成或补画形态细节。
