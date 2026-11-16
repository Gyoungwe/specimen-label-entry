# 精细裁边与比例尺去底

用于矩形裁切带进标签边框、残字或邻组碎片，以及透明尺仍带纸边、阴影或异物的情况。先查看原像素裁切；能在保留全部触角、足、翅缘和本组标签的前提下重选框时，直接修改分组计划，另导出一版。已经截断的形态应回原图扩框，背景清理不能恢复缺失形态。

需要非矩形处理时，保留原始证据，另存清理副本。`reviewed: true` 表示执行本批任务的模型已看图检查蒙版，不代表用户完成人工核对。所有脚本路径相对 skill；安装后可使用 `python scripts/run.py HELPER.py ...`。

## 先核对坐标

```text
python scripts/crop_cleanup.py prepare WORK/split-final/groups-manifest.json --out WORK/crop-review
```

查看生成的完整组图、形态图和共享尺坐标图。`crop-review.json` 提供各文件的 SHA-256 与原尺寸。边缘深色提示只帮助定位黑边或可能被截断的细肢，不能直接授权擦除。

本节蒙版使用 **局部裁切的原尺寸像素** `crop_pixels`，原点在该局部图左上角；多边形为 `[[x,y], ...]`，至少三点，坐标在图内。它不同于分组计划的整图 `normalized_0_1000`，不能把缩小预览中的像素直接填入。重新裁切后尺寸或哈希变化，重新做蒙版；脚本拒绝来源不符的计划。

## 比例尺精细蒙版

先提取默认草稿并看白底、深底预览：

```text
python scripts/shared_scale.py extract WORK/split-final/groups-manifest.json --out WORK/scale-draft
```

查看 `scale-white-dark.png`，放大数字、单位、每个端点。透明文件中 alpha 为零的隐藏 RGB 不会显示；直接查看隐藏 RGB 容易误判为去底失败。

若仍有残留，写 `scale-mask.json`。以下坐标仅示意结构，实际按本张尺的原尺寸填写：

```json
{
  "source_sha256": "从 shared_scale_review 复制",
  "image_size": [300, 160],
  "coordinate_space": "crop_pixels",
  "reviewed": true,
  "include_polygons": [[[20, 45], [280, 45], [280, 150], [20, 150]]],
  "exclude_polygons": [[[140, 45], [155, 45], [155, 55], [140, 55]]],
  "protected_polygons": [[[25, 110], [275, 110], [275, 145], [25, 145]]],
  "review_note": "填写排除的实际异物、墨迹保护依据及交叠问题"
}
```

`include_polygons` 可选：有值时仅保留圈内原墨迹；`exclude_polygons` 去除确认的异物。`protected_polygons` 优先保留其中的原墨迹，覆盖排除区与保留区外沿。至少给出保留区或排除区；默认深色提取不能自动区分黑色异物与真实刻度。精细蒙版仅改变透明度，不移动、缩放、重画墨迹，保留下来的 RGB 仍来自原尺。

```text
python scripts/shared_scale.py extract WORK/split-final/groups-manifest.json --scale-mask WORK/scale-mask.json --out WORK/scale-reviewed
```

再看白底和深底版本，确认残留减少且全部数值、单位与端点完整。异物与真实墨迹交叠时，保留无法分离的像素并在 `review_note` 标记，不补画刻度。不能区分时保留原尺与待核对说明。

## 裁边清理副本

完整组图保留该馆藏号的全部标签，只有确认的邻组残片才排除。单独形态图可排除混入的编号框或采集标签。写 `cleanup-plan.json`，顶层哈希取自 `crop-review.json`；每项尺寸和源哈希取对应条目：

```json
{
  "source_manifest_sha256": "从 crop-review.json 复制",
  "entries": [{
    "specimen_number": "IOZ(E)0000001",
    "target": "specimen",
    "source_sha256": "对应原像素裁切的 SHA-256",
    "image_size": [600, 400],
    "coordinate_space": "crop_pixels",
    "reviewed": true,
    "exclude_polygons": [[[0, 350], [599, 350], [599, 399], [0, 399]]],
    "protected_polygons": [
      [[70, 30], [500, 30], [500, 330], [70, 330]],
      [[155, 320], [160, 320], [165, 390], [160, 390]]
    ],
    "background_samples": [[10, 290, 50, 330], [520, 270, 570, 320]],
    "protection_margin": 3,
    "feather_radius": 4,
    "review_note": "按实际原图填写背景样区及触角、足、翅缘保护依据",
    "unresolved_overlap": "如有交叠，填写保留位置与原因"
  }]
}
```

`target` 可以是已有的 `group`、`specimen`、`collection`、`catalogue`、`identification`。只列确需清理的目标。保护区包住全部实体及细小附肢；窄触角用贴合轮廓的多边形，避免整条粗矩形保留大片标签黑边。`background_samples` 为明确查看过的空白原图区域，整数 `[左,上,右,下]`，右下边界不包含；避开文字、实体、深阴影与纸片边界。

```text
python scripts/crop_cleanup.py apply WORK/split-final/groups-manifest.json WORK/cleanup-plan.json --out WORK/cleaned
python scripts/crop_cleanup.py verify WORK/cleaned/cleanup-manifest.json
```

程序仅在排除区及羽化边缘混入原图空白处的颜色与原尺寸纹理，保护区优先。`protection_margin` 为 0–30 像素，`feather_radius` 为 0–10 像素；对细触角小范围调整并重看实际输出。清理区外与保护区内的像素逐项检查不变，画布尺寸不变。副本的背景已经处理，应与原像素证据分开命名；脚本证明所声明的保护区不变，形态是否全部圈入仍由看图判断。

触角本身跨过标签黑边时，交叠区域保留原像素，可能留下很小的黑点或纸色。记录 `unresolved_overlap`，不要为了全无残痕而擦掉触角或补画真实形态。若原尺已在完整组图中且也有异物，可另列 `target: group` 只清理该异物；保留本组标签及真实尺墨迹。

## 合成与交付

将已检查的蒙版和清理清单接入原叠尺流程；未做某项时省略相应选项：

```text
python scripts/shared_scale.py add WORK/split-final/groups-manifest.json --scale-mask WORK/scale-mask.json --cleanup WORK/cleaned/cleanup-manifest.json --out WORK/带比例尺分组
python scripts/shared_scale.py verify WORK/带比例尺分组/compositions-manifest.json
```

空白不足时，扩展背景从明亮、低对比的边缘空白处取用颜色与原尺寸纹理，避免把底边标签或阴影拉成长条；无法取得可用样区时采用亮部估计色。检查扩展处与原照片的衔接，扩展背景不是原始证据。

验证会重建清理副本、透明尺及每张合成图，检查原始裁切、保护区、局部尺寸与 1:1 刻度几何，并检测副本或计划被改动。旧版清单继续按原提取和背景算法验证。原始像素裁切一直保留，录入仍以原标签为依据。

交付原图、原像素裁切、清理副本、计划、透明尺白底/深底预览和验证清单。按实际馆藏号数量检查覆盖，不设数量上限。逐张看清理与合成结果，报告未能无损分离的交叠，`human_review_pending` 保持为真。
