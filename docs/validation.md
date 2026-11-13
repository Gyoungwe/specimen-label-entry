# v3.3 验证范围

本文记录 v3.3 发布时已执行的检查，避免将文件安装、脚本检查和模型识图混为一谈。

## 已执行

| 层次 | 观察结果 |
| --- | --- |
| 核心脚本 | 49 项测试通过，覆盖分组、原像素裁切、共享比例尺、网格、固定规则、模板与写表 |
| 安装器 | 5 项测试通过，覆盖三宿主复制、备份、后续宿主失败时回滚、项目级路径与发布校验 |
| 独立依赖环境 | macOS 上独立 Python 3.14 venv；Pillow 13.0.0、NumPy 2.5.4、openpyxl 3.1.5、lxml 6.1.3 |
| Codex 发现 | 本地 app-server `skills/list` 返回同名 user skill，`enabled=true` |
| Claude Code 发现 | SDK 初始化命令目录列出同名 user skill；探测时未发送模型提示词 |
| DeepSeek Harness 发现 | 实际本地文件 provider 返回 `user-dsh`，成功读取内容和版本 3.3.0 |
| 十二枚样例写表 | 三个安装副本分别运行 Python 后端，每份读回 768 个单元格，15 条原生模板验证保留 |
| Excel 部件保真 | 填写前副本 SHA-256 与参考表一致；除目标工作表与样式外，其余 ZIP 部件逐项保留原始字节 |
| 表格视觉检查 | Codex 表格组件渲染主表与两张核对表的 6 处视图并查看 |
| 导出 | skill 和两个 ZIP 均通过 CRC 与内容校验，安装副本与导出核心一致 |

数量流程测试包含 1、4、12、37 组的合成图像裁切与叠尺，以及 128 组的计划格式校验。它们不构成大量真实标本的识图准确率评测。

250 条记录会按附带模板的验证范围分为 249 + 1，两个分册分别检查本地 Excel 行与原生验证规则。

## 样例中仍需人工处理的项目

十二枚照片的既有标签转录用于测试写表；本轮没有重新让三种模型识别该照片。已有待核对项保留：

- 12 枚未见鉴定标签，学名与鉴定字段留空。
- 12 枚的标签坐标未被附带网格覆盖，网格号留空。
- 12 处“完整”与模板选项“1.完整”冲突，保留用户值并标记。

结构和数值检查通过，不意味着这些问题已经人工解决。

## 未执行

- 三个模型对同一批照片的独立识图准确率对比。
- Claude 桌面端/Cowork 账号中的导入和实际会话执行。
- Microsoft Excel 原生应用打开测试；HTML 数据预览不替代原生排版检查。

具体模型能否完成看图、标签归属和转录，需在选定宿主、模型及视觉工具组合下另行实测。共享尺像素保持与物理尺度标定也需要分别判断。

## 持续验证

仓库的 GitHub Actions 会在干净的 Linux 环境中运行核心脚本与安装测试，覆盖 Python 3.10、3.12、3.14。实际运行状态以 [Actions](https://github.com/Gyoungwe/specimen-label-entry/actions/workflows/tests.yml) 为准。

开发时运行：

```bash
python -m pip install -r specimen-label-entry/requirements.txt
python install.py --host all --dry-run
python -m unittest discover -s specimen-label-entry/scripts -p 'test_*.py' -v
python -m unittest discover -s tests -v
```
