# 给 agent 的工具入口

这些工具是可移植的本地命令，Codex、Claude、DeepSeek 等带终端的 agent 使用同一入口，无须另搭 MCP 服务或配置模型密钥。识图和最终判断由实际看图的 agent 完成；脚本接管重复计算、文件操作与证据校验。

所有命令通过安装环境运行：`python scripts/run.py photo_workflow.py ...`。将这里相对 skill 的脚本路径解析为绝对路径。输入和工作目录可在任意位置；批次 JSON 内相对路径相对于该 JSON 的目录解析。

| 子命令 | 输入 | 结果与下一步 |
| --- | --- | --- |
| `prepare` | 原片、输出目录、可选阅读转向及实际编号清单 | 无框原片预览、原像素坐标网格、计划草稿；检查标志初始均为 false |
| `check` | 原片与计划 | JSON 列出缺哪个角色、哪个馆藏号和失败原因；回原片修订 |
| `build` | 单张原片与已复核计划 | 原像素证据、双比例尺候选图、分页联系表、尺的明暗预览和核对模板 |
| `batch` | 多张原片任务清单 | 每个任务独立记录结果；有失败仍处理其余任务，整批不会被误报为已完成 |
| `review` | 候选清单与实际看图后的记录 | 五项检查绑定最终图片哈希；执行者写 agent 名称，不冒充用户确认 |
| `verify` | 已核对清单 | 重放原片裁切、清理和叠尺，验证检查状态及图片字节 |
| `export` | 记录 JSON 与所有完整清单 | 强制完整验收后按馆藏号命名复制；冲突、缺项和未确认检查会失败 |

## 使用顺序

```text
python scripts/run.py photo_workflow.py prepare PHOTO.JPG --out WORK/photo-01 --rotation 180
```

先打开返回 JSON 中的 `overview` 和 `coordinate_grid`，确认方向。网格数字是 EXIF 转正并按 `--rotation` 转向后的原图像素，不能用预览像素代替。读取实际编号后编辑生成的 `plan.json`；参数 `--ids` 只方便放入已读清单，不会自动确认编号或配对。组件、原生尺、整纸和附肢范围按 [五项完整验收](photo-completeness.md) 标注。

```text
python scripts/run.py photo_workflow.py check PHOTO.JPG WORK/photo-01/plan.json
python scripts/run.py photo_workflow.py build PHOTO.JPG WORK/photo-01/plan.json --out WORK/candidate-01
```

`ready_to_build` 仅代表源计划通过检查。`build` 返回 `needs_final_visual_review`、联系表路径、每枚尺的明暗预览及 `reviews-template.json`。模板中的五项全部为 false；逐图对照原片确认后另存 `reviews.json`，再执行：

```text
python scripts/run.py photo_workflow.py review WORK/candidate-01/complete-manifest.json WORK/reviews.json
python scripts/run.py photo_workflow.py verify WORK/candidate-01/complete-manifest.json
```

修改候选图会使原检查失效，需要重新生成和重新看图；不能只换哈希继续勾选。出现原片模糊/遮挡/画幅外，保留待核对，不把 false 批量改为 true。

多照片任务文件示例（数量不限）：

```json
[
  {"source":"photos/A.JPG","plan":"plans/A.json","out":"runs/A-01"},
  {"source":"photos/B.JPG","plan":"plans/B.json","out":"runs/B-01"}
]
```

```text
python scripts/run.py photo_workflow.py batch WORK/jobs.json --report WORK/batch-report.json
```

批次报告保存每张来源、计划、输出、阶段及错误。检查失败只修订该任务并使用新的输出目录；保留前一轮证据，不循环缩小组件，也不把中断留下的半成品当作成功。默认不会替换已有目录或覆盖输入。

导出入口会按完整馆藏号和来源文件自动绑定图片，替换新记录副本中的旧裁切引用，免去逐条编辑 provenance。每条 `source` 必须对应 `sources` 中的实际照片路径。编号重复、来源不一致、漏图或尚未复核时停止；标签与其他数据值保持原样。执行：

```text
python scripts/run.py photo_workflow.py export WORK/records.json --out WORK/delivery/图像 --records-out WORK/delivery/records.json --manifests WORK/candidate-01/complete-manifest.json WORK/candidate-02/complete-manifest.json
```

后续网格、纠错和写表继续使用 `specimen_data.py`、`build_workbook.py` / `build_workbook.mjs`、`verify_workbooks.py`；主表始终先复制参考 Excel 后填写。图像入口不会根据照片布局猜编号、鉴定或整批采集信息，也不会自行改变网格和区间值规则。

## 透明尺仍有残影

`complete_photos.py` 支持在完整原尺 ROI 上用 `ink_seed_boxes` 选择字、单位和刻度的连通笔画。seed 是选择区域，整条笔画会延伸保留，不会沿 seed 边界截断。点选已识别的独立固定针或纸边 `ink_drop_points` 可只去掉该连通组件，避免矩形排除框擦掉邻近字。源计划需声明 `ink_seeds_reviewed`；删除组件另需 `ink_mask_reviewed` 和实际检查说明。坐标均为原片转正、阅读转向后的原像素。

这个步骤只适用于已看清的尺墨迹与异物；物理连在一起的刻度与异物会一起传播，必须回到精细多边形检查。明暗预览和原尺对照仍要逐一完成，像素重放不证明选对了笔画。
