# MarkItDown Desktop

把 [Microsoft MarkItDown](https://github.com/microsoft/markitdown) 包装成 **Windows 桌面应用**：
拖进文档 → 得到 Markdown。**免安装、免 Python、免命令行。**

> *A Windows desktop GUI for MarkItDown. Drag documents in, get Markdown out — no Python, no command line.*

---

## 来源与许可声明（请先读这一段）

- **本项目不是微软官方项目**，与 Microsoft 及 MarkItDown 上游团队**无隶属、无背书关系**。
- **转换能力来自上游开源项目 MarkItDown**：<https://github.com/microsoft/markitdown>（PyPI 包名 `markitdown`），
  以 **MIT** 许可发布。本项目**通过 PyPI 依赖它**（`markitdown[pdf,docx,...]>=0.1.8,<0.2.0`），
  **没有复制、没有修改上游源码**；所有格式解析、内容提取的行为与缺陷都归属上游。
- **本项目自身代码同样以 MIT 许可发布**，全文见 [LICENSE](LICENSE)。
- **随包分发的第三方组件**（Qt/PySide6、pandas、numpy、Pillow、lxml、magika、onnxruntime、
  pdfminer.six、python-docx、openpyxl 等）及其许可证全文见
  [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) 与 `licenses/` 目录。
  其中 **Qt/PySide6 为 LGPL-3.0**，动态链接使用、未修改其源码，合规说明见通告第 3 节。
- **名称与商标**：`MarkItDown` 是上游项目的名称，本项目仅用它说明来源与用途，不主张任何权利。
- 上游明确把桌面 GUI 列为 *out of scope*，但**明确鼓励**以「独立项目 + 依赖 PyPI 上的 `markitdown`」
  的方式实现 —— 本项目就是这条路径。

---

## 它解决什么问题

上游 `markitdown` 是个 Python 库 / 命令行工具，对不写代码的人不可用：要装 Python、开命令行、
记参数。本项目把它变成**双击就能用的 Windows 程序**：拖文件、点开始、拿到 `.md`。

## 功能

| 功能 | 说明 |
| --- | --- |
| 拖拽批量转换 | 一次拖几十个文件；文件列表、单文件状态、进度、耗时 |
| 真正的取消 | 转换跑在独立进程池里，点取消**硬杀 worker**（实测 **0.03 s** 生效），不是"等它跑完" |
| 超时与崩溃隔离 | 单个文件超时可设阈值并硬杀；某个文件崩了不影响其余文件 |
| 并发可控 | 并发度 1–4（默认 2），每 +1 约 +140 MB 内存 |
| 重名不覆盖 | 两种命名策略：自动加序号（`a (1).md`）或保留扩展名（`a.docx.md`） |
| 空结果警告 | 图片等需要 LLM/exiftool 的场景会返回"成功但 0 字符"，界面显式提示，不静默产出空文件 |
| 失败可重试 | 「重试失败项」只重跑失败行，成功行的结果与预览保留 |
| 批量另存为 | 选中多行「另存为 .md…」→ 选一个文件夹，按原文件名批量写出，重名自动跳过 |
| 设置对话框 | 输出目录、重名处理、并发数、自动写出、界面语言，改完即生效并持久化 |
| 中英双语 | 界面中/英切换（含错误提示；日志保持中文便于排查） |
| 失败可追溯 | 日志落盘 `%LOCALAPPDATA%\MarkItDownDesktop\logs\app.log`，失败只记「路径 + 原因」，**不记文件内容**，可一键导出 |
| 不留孤儿进程 | 主程序被强杀（含**启动瞬间**）时，worker 在 1~2 s 内自退 |

## 支持的格式

`markitdown` 能转的都能转（PDF、Word、PowerPoint、Excel、CSV、HTML、EPUB、Outlook MSG、
JSON、XML/RSS、Jupyter Notebook、文本类等），外加**含链接图片的 docx** 等边界场景。
上游支持范围见其 README；本项目的实测矩阵见下节。

刻意**不使用** `markitdown[all]`（会拖入 Azure SDK 与约 31 MB 的 `SpeechRecognition`）：

```toml
"markitdown[pdf,docx,pptx,xlsx,xls,outlook,youtube-transcription]>=0.1.8,<0.2.0"
"PySide6-Essentials>=6.8"    # 不用 PySide6-Addons（省约 39 MB）
```

音频转写作为可选 extra（`pip install .[audio]`），因为它还需要外部 `ffmpeg`。

## 实测数据（Windows 11 x64，Python 3.12，PyInstaller 6.22.3）

| 指标 | 实测值 |
| --- | --- |
| one-folder 产物 | **211.5 MB**（默认裁剪档 `safe`） |
| Inno Setup 安装包 | **64.17 MB**（per-user，免 UAC） |
| 免安装 zip | **93.04 MB**（解压即用，不写注册表） |
| 冷启动（进程启动 → 窗口可交互） | **0.947 s**（3 次中位数） |
| GUI 常驻内存 | **160.2 MB**（并发 2 峰值 438.4 MB） |
| 取消响应 | **0.03 s**（硬杀 worker） |
| 格式矩阵 | 13/13 夹具通过，输出字符数与上游逐字节一致 |

## 快速开始（开发运行）

```powershell
# 1) 建环境（需要 Python 3.12）
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"

# 2) 直接运行 GUI
.venv\Scripts\python.exe -m markitdown_desktop

# 3) 无界面自测（输出 JSON 报告，用于测量启动与转换耗时）
.venv\Scripts\python.exe -m markitdown_desktop --selftest test.pdf test.docx --offscreen --json report.json
```

## 打包

```powershell
# one-folder（默认 safe 裁剪档）
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\markitdown-desktop.spec

# 可选裁剪档：none | safe | aggressive（明细写入 dist\trim-report.txt）
$env:MDD_TRIM = "aggressive"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\markitdown-desktop.spec

# 出安装包（PyInstaller → Inno Setup → 体积检查；需要 Inno Setup 6）
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1

# 出免安装 zip（复用已有 one-folder 加 -SkipPyInstaller）
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
```

### 体积裁剪（`MDD_TRIM`）

| 档位 | one-folder | 说明 |
| --- | --- | --- |
| `none` | 233 MB | 基线，用于对比 |
| **`safe`（默认）** | **213 MB** | 只裁 UI 渲染与网络栈用不到的文件：Qt 翻译（保留 en/zh_CN）、Pillow AVIF、QtNetwork、sqlite3、Qt TLS/网络信息插件、部分图像格式插件 |
| `aggressive` | 192 MB | 额外裁 `opengl32sw.dll`（20 MB 软件 OpenGL 回退）与 `qdirect2d`；**无 GPU 的 VM/RDP 未验证** |

> **裁剪原则：只减冗余，不减功能。** `magika`/`onnxruntime`（内容嗅探）、`pandas`/`numpy`（Excel）、
> PDF 栈、Office 栈、`lxml` 等**全部保留**，不改上游行为、不降级依赖。
> 两档裁剪的格式支持与未裁剪基线**完全一致**（同一套用例全过）。

## 项目结构

```text
src/markitdown_desktop/
  __init__.py        # 最先执行 _compat.preload_heavy_deps()
  _compat.py         # shiboken6 × six.moves 导入冲突修复（见下方"坑"）
  engine.py          # ConversionEngine：上游 convert 封装、错误明细、编码重试、UTF-8 写盘
  worker.py          # ConversionPool：进程池（硬杀取消、超时看门狗、崩溃隔离）
  _child.py          # 子进程入口（零 Qt 导入）+ 父进程看门狗
  settings.py        # AppSettings：输出目录/命名策略/并发度/窗口几何/语言 持久化
  i18n.py            # 中英字符串表（零 Qt、零第三方依赖，子进程不导入）
  logging_setup.py   # 日志落盘 + 全局异常钩子 + 一键导出
  settings_dialog.py # 设置对话框
  ui.py              # MainWindow：拖拽队列 + 进程池 + 取消 + 预览 + 关闭确认
  __main__.py        # GUI 入口 + --selftest 自测模式（inproc / pool）
packaging/
  run.py                   # PyInstaller 入口：装看门狗 → freeze_support()（失败即 os._exit）→ main()
  markitdown-desktop.spec  # one-folder 打包配置（magika 数据 / 元数据 / 体积裁剪）
  installer.iss            # Inno Setup 安装包脚本（per-user 免 UAC，需带 UTF-8 BOM）
  languages/               # 简体中文向导语言文件（Inno 自带语言里没有中文）
  licenses/                # 上游没带正文的包的补充许可证 + SOURCES.md 来源登记
scripts/
  build_installer.ps1        # 一键：PyInstaller → Inno Setup → 体积检查
  build_portable.ps1         # 一键：PyInstaller → 补合规文件 → Deflate zip → 体积检查
  gen_third_party_notices.py # 生成 THIRD-PARTY-NOTICES.md + licenses/
  p0_verify.py / p1_verify.py # 转换→写盘→失败路径 / 冻结包行为验证（超时、隔离、日志、无残留进程）
  smoke_matrix.py            # 源码/冻结 × 并发 1/2 冒烟矩阵，逐文件比对字符数
THIRD-PARTY-NOTICES.md     # 【生成物，勿手改】第三方通告
licenses/                  # 【生成物，勿手改】一包一目录的许可证正文
```

## 架构：三层解耦

| 层 | 文件 | 约束 |
| --- | --- | --- |
| 纯逻辑 | `engine.py` | **不导入 PySide6**，可脱离 GUI 单测 |
| 并发 | `worker.py` + `_child.py` | **不导入 Qt**；用 `multiprocessing(spawn)` 而不是 `QThread`，才能硬杀 |
| 展示 | `ui.py` | 只做展示与交互，不做转换 |

为什么用进程池而不是线程：转换是 CPU/IO 混合的重活，且上游库在异常时可能残留状态；
**能硬杀**是"取消要立刻生效"和"崩溃要隔离"的前提（P0 的 `QThread` 版做不到，实测取消要等它自己跑完）。

## 已知边界（没做 / 没验证的，不写成"已支持"）

- **没有代码签名** → 首次运行会弹 SmartScreen，杀软可能误报，这是当前最大的用户侧信任问题；
- **没有自动更新**（下载+静默升级涉及 UAC 与回滚，风险大于收益）；
- **无 GPU 的 VM / RDP** 上未验证 `aggressive` 裁剪档（缺 `opengl32sw.dll`）；
- **音视频转写、图片 OCR/LLM 描述**未做（需要外部二进制与密钥存储）；
- 只在 **Windows 11 x64** 上实测；其他 Windows 版本、其他架构未验证。

## ⚠️ 必须知道的坑：shiboken6 × `six.moves` 导入顺序冲突

**症状**：PySide6 先于 pandas 导入时程序直接崩溃。

```powershell
python -c "from PySide6.QtWidgets import QApplication; import pandas"
```

**原因**：shiboken6 的导入钩子会调 `inspect.getsource()`，触发 `six` 的元路径导入器
`AttributeError: '_SixMetaPathImporter' object has no attribute '_path'`。冻结包里因为
`pyi_rth_pyside6` 会提前导入 PySide6，问题更隐蔽。

**处理**：`_compat.py` 给 `six._importer` 补 `_path`（与导入顺序无关，源码与冻结包同时生效），
并保留"先 pandas 后 PySide6"的顺序作第二道保险。若你也遇到，建议一并向上游 PySide6 报障。

## 复现构建与验证

```cmd
REM 转换 → 写盘 → 失败路径全流程
.venv\Scripts\python.exe scripts\p0_verify.py

REM 冻结包行为验证（超时硬杀 / 失败隔离 / 日志 / 无残留进程），输出 p1-verify.json
.venv\Scripts\python.exe scripts\p1_verify.py

REM 冒烟矩阵：源码/冻结 × 并发 1/2，逐文件字符数一致性
.venv\Scripts\python.exe scripts\smoke_matrix.py
```

## 致谢

- [Microsoft MarkItDown](https://github.com/microsoft/markitdown) —— 全部转换能力来自它；
- [Qt / PySide6](https://www.qt.io/) —— 界面；
- 以及 pandas、numpy、Pillow、lxml、magika、onnxruntime、pdfminer.six、python-docx、openpyxl 等
  上游依赖的作者们。完整清单与许可证见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

## 许可证

MIT，见 [LICENSE](LICENSE)。本项目是独立第三方项目，与 Microsoft 无关。
