# 完整图片交付：五项逐张验收

交付照片、旧图缺件、纸边被截断或用户要求同时显示原生尺与合成尺时使用。检查对象是实际生成的每张图，按真实馆藏号逐号覆盖；照片中有多少枚就处理多少枚。

## 验收依据

| 项目 | 完整的含义 |
| --- | --- |
| 标本号 | 本枚的完整编号与整张编号纸；只保留可读数字仍不合格 |
| 标本图 | 头、翅、足及可见触角末端均保留；长触角可跨越名义行列边界 |
| 采集标签 | 整张纸及四边、角、最后一行均保留；OCR 文本框和黑色边框不能代替纸张外边界 |
| 原生比例尺 | 完整保留拍摄到的数字、单位和全部刻度；同片共享尺位于远处时，另放完整原尺纸片裁图并记录“同片裁取”，不冒称每枚原本自带 |
| 合成比例尺 | 从该枚原尺或同一原片的共享尺去底提取，数字、单位、刻度齐全，无邻物和纸边残影，几何比例 1:1 |

用户要求五项齐全时，原尺已经出现在组图中，仍需提供独立的合成尺。原尺缺失时列出待补拍项，不从别的照片借尺，也不绘制替代尺。整张照片的共享尺复制不是物理标定，保留共面与透视复核状态。

先看原片，标注各项完整区域，再生成并逐张看结果。组件框应圈住真实完整区域和必要余量；不要把整组框同时冒充三个组件。预览可以缩放，正式图仅作整数裁切及声明的直角转向。

## 方向和边界

先明确三个空间：相机存储像素、EXIF 转正图、为了阅读另转向的图。`photo_groups.py` 的框属于 EXIF 转正图；下面新工具的框属于 **EXIF 转正后再执行 reading_rotation 的原尺寸图**。左上原点，横向乘宽，纵向乘高；Vision 等 OCR 的左下原点要先换算。预览坐标按真实预览宽高换回，不能直接填入原像素框。

EXIF 旋转、阅读转向、OCR 原点或来源框一旦变化，从原片重建所有受影响组，旧裁框失效。不能用旧图的文件大小、非空检查或相同记录数替代重看。

行列只辅助定位。纸张斜放、同列行距不齐、跨行触角、重复编号均逐枚处理。两个矩形仅接触边缘时相交面积为零，不需要制造会截纸的间隙。发生真正重叠时保留完整目标和重叠记录；采用直接截取或精细清理副本，不能反复缩小标本/标签来让校验通过。

## 构建与核验

使用 `complete_photos.py` 从已视觉确认的原片计划生成原像素证据、五项齐全的候选交付图和清单。计划示例（坐标须替换为该原片实际位置）：

```json
{
  "source_sha256": "原片 SHA-256",
  "exif_display_size": [7008, 4672],
  "reading_rotation": 180,
  "oriented_size": [7008, 4672],
  "coordinate_space": "oriented_native_pixels",
  "expected_groups": 1,
  "native_scale": {
    "box": [80, 80, 650, 420],
    "ink_box": [100, 120, 620, 400],
    "label_text": "10 mm"
  },
  "groups": [{
    "specimen_number": "IOZ(E)0000001",
    "group_box": [900, 500, 2700, 1900],
    "parts": {
      "catalogue": [1700, 550, 2300, 750],
      "specimen": [950, 800, 1550, 1750],
      "collection": [1600, 800, 2650, 1850]
    },
    "pairing_reviewed": true,
    "source_review": {
      "reviewer": "实际看图的执行者",
      "catalogue": true,
      "specimen": true,
      "collection": true,
      "native_scale": true,
      "note": "记录实际检查的纸张四边、附肢末端、原尺刻度与归属"
    }
  }]
}
```

每枚各有原尺时，将 `native_scale` 放在各组中。`box` 保留完整原尺纸片，`ink_box` 是包含全部有效尺墨迹、排除纸边与异物的同片区域。若墨迹和异物交叠，读 [精细清理](precision-cleanup.md)；不能为了干净而删除刻度。

```text
python scripts/complete_photos.py build PHOTO.JPG WORK/complete-plan.json --out WORK/complete-01
```

原生尺已在完整组图内时保留，再把合成尺放到不覆盖保护区的空白处；空白不足才延展背景。原生尺在远处时，同时添加完整原尺裁图与透明合成尺，两者均保持原像素尺寸。原始连续裁图保留在 `raw/`；比例尺资产保留在 `scales/`。

生成后逐张看图，密集图先联系表筛查，再对疑点放大回原片。将真实检查记录写入列表，每项绑定候选图的 SHA-256：

```json
[{
  "specimen_number": "IOZ(E)0000001",
  "image_sha256": "该张生成图的 SHA-256",
  "reviewer": "实际执行者，区分 agent 与用户",
  "catalogue": true,
  "specimen": true,
  "collection": true,
  "native_scale": true,
  "synthetic_scale": true,
  "note": "逐张检查后的实际结论"
}]
```

```text
python scripts/complete_photos.py review WORK/complete-01/complete-manifest.json WORK/final-reviews.json
python scripts/complete_photos.py verify WORK/complete-01/complete-manifest.json
```

`verify --pixels-only` 只允许在候选阶段检查；未写最终视觉检查、漏一项、图已变更、裁框截断已声明区域或像素重建不一致，不能作为完整交付通过。`reviewer` 如为 agent，不代表用户已人工确认。程序核对的是已声明证据，不能自动判断组件框是否真的包含整张纸和触角。

记录的 `image_provenance.complete_photo.path` 指向该枚最终图，来源和完整馆藏号保持正确，再导出：

```text
python scripts/photo_delivery.py export WORK/records.json --out WORK/delivery/图像 --records-out WORK/delivery/records.json --compositions WORK/complete-01/complete-manifest.json --require-complete
```

多照片传入全部清单。`--require-complete` 会拒绝未完成五项视觉检查的旧裁图。先输出到新目录，再备份和替换旧交付；检查实际交付文件与审核图哈希一致、315 等实际记录数逐号覆盖、无重号或多图。表格和数据字段不因图像修复而自动改写。

## 发现缺件后

按馆藏号列出来源照片、缺哪项、原片位置和本轮处理。先尝试原片重裁、转向核对、完整区域或重拍来源。原片确实遮挡或超画幅时如实标记，不补画标签纸、触角或刻度。需要去掉相邻残片时，仅修改有记录的背景清理副本，保留原始连续裁图；真实交叠不能无损分离时保留并明确列出。

错误旧图和旧清单保留在备份中。修复结果写入新清单及实际交付位置；“运行成功”“已写文件”“0 空白图”和单元格校验通过均不能充当五项视觉验收结论。
