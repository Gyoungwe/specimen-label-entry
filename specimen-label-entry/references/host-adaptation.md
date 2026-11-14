# 宿主适配

三个宿主使用同一份规则、JSON 结构、图像处理与验证脚本。模型负责看图、关联标签、逐字段判断清晰/模糊/破损/缺失并比对参考；脚本核验 [跨标签纠错](label-corrections.md) 的结构化证据，再负责裁切、比例尺、网格、写表与检查。安装方式不证明模型的识别准确率。

## 通用运行

先取得宿主给出的 skill 资源根目录，把相对脚本和资源路径解析为该目录下的绝对路径。保持用户的本批工作目录独立。路径中有空格或中文时始终作为单独命令参数传入。

运行 `python scripts/run.py doctor.py`；PDF 分支增加 `--require-pdf`。报告检查 Python、Pillow、NumPy、openpyxl、lxml、规则、模板、网格，以及可选的 Poppler/Swift；不调用模型。Python 最低 3.10。Unix 通常使用 `python3`，Windows 可以使用 `py -3` 运行入口。

安装器在用户目录创建独立 venv，并把解释器写入每个安装副本的 `runtime.local.json`。`run.py` 优先使用 `SPECIMEN_PYTHON`，其次该配置，最后当前解释器。配置与 venv 不进入导出包，迁移机器后重新安装。

导入型或受限环境没有本地配置时，先检查当前 Python 的依赖。缺包且环境允许安装时，在本批工作目录创建 venv，安装 skill 的 `requirements.txt`，再将 `SPECIMEN_PYTHON` 指向该 venv 的解释器。不要复用导出者机器的绝对路径。PDF 渲染另需 `pdftoppm`；照片流程不依赖 Poppler。macOS Swift OCR 是可选候选来源，其他系统使用宿主看图或已有 OCR。

宿主应提供原图及局部图的实际查看能力，或配置可用的 OCR/视觉工具。仅能读文本的模型不能独立验证标本与标签归属。未看清的字段先保存原始读数与质量；经核验的清晰参考可补全共享采集字段，没有证据或有冲突则留空并定位给用户。大量标本分区域处理，合并馆藏号清单后核对覆盖，不能只接受固定数量或固定行列。

## Codex

用户级兼容安装位置是 `${CODEX_HOME:-~/.codex}/skills/specimen-label-entry`。安装器同时在 `~/.agents/skills/specimen-label-entry` 建立指向该副本的发现链接；已存在其他同名条目时保留并在安装回执提示。项目级安装使用 `.agents/skills/specimen-label-entry`。

可直接使用 `$specimen-label-entry` 或描述标本入库任务。内置表格组件可用时遵循表格技能，使用 `build_workbook.mjs`；组件缺失的环境使用 `build_workbook.py`。两者使用同一份 `compiled.json` 和 `verify_workbooks.py`。内置运行时仅在当前 Codex 环境发现后使用，导出包不写死其位置。

## Claude Code

用户级安装到 `~/.claude/skills/specimen-label-entry`，项目级安装到 `.claude/skills/specimen-label-entry`。用 `/specimen-label-entry` 调用。使用该次加载的资源根目录；支持 `${CLAUDE_SKILL_DIR}` 的宿主可用其定位，其他宿主按加载信息定位。

通过宿主看图工具查看原图及裁后分组，再逐组识别。默认使用 `python scripts/run.py build_workbook.py ...` 写表，保留原模板副本、原有验证及所有未修改部件。无需配置 OpenAI API，也无需访问 Codex 的缓存目录。

## Claude 桌面端 / Cowork

本地 `~/.claude/skills` 安装只覆盖 Claude Code，不等同于桌面端或 Cowork 账号中的 skill。通过 Claude 的 Customize / Skills 导入独立 `.skill` 或 ZIP，启用后在有文件读写与执行能力的会话使用。具体界面名称以当前客户端为准；本地安装器不会修改账号设置。

导入包只包含一个 `specimen-label-entry/` 根目录与完整资源，不包含安装器、运行时配置、venv、用户照片或结果表。导入后在实际执行环境运行 doctor；执行脚本使用 Python 后端。若会话缺少文件执行或图像能力，明确说明缺失能力，不能把纯聊天抄写宣称为完整自动入库。

## DeepSeek Harness

用户级安装到 `${DSH_HOME:-~/.dsh}/skills/specimen-label-entry`，项目级安装到 `.dsh/skills/specimen-label-entry`。官方本地 provider 也读取 `.agents/skills`；同名项目级条目可能优先于用户级条目。目录必须直接包含 `SKILL.md`，不要嵌套两层技能文件夹。

在技能菜单或支持直接调用的会话中使用 `/specimen-label-entry`，或让模型通过 `skill` 工具加载同名技能。宿主需启用本地技能 provider、技能加载工具、文件/终端与实际图像查看能力。技能目录已安装不代表当前模型支持视觉输入；先用原图或明确的小样确认它能实际看图。

默认使用 `python scripts/run.py build_workbook.py ...`，不依赖 Codex MCP 或 Node 包。安装器不改模型、API key、工具权限、插件或全局配置；在新会话检查目录发现和加载结果。

## 一致性验收

每批保留原图哈希、实际分组数、裁切与叠尺验证、原文转录、规则化记录、主表、核对清单及读回验证。Python 后端保存填写前复制哈希，并逐项确认除目标工作表和样式外的 XLSX 部件保持原始字节。日期保存为可排序日期，编号为文本，人工核对栏初始空白。已存在的输出文件一律选择新名字。

版本发布分别报告：文件部署、依赖检查、宿主发现/加载、脚本流程、模型识图实测。未执行的层次写明未执行，不能用目录存在或脚本测试替代三种模型独立实测。
