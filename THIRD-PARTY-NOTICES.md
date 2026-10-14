# 第三方软件通告

> 本文件由 `scripts/gen_third_party_notices.py` 自动生成，**请勿手工修改**。
> 重新生成：`.venv\Scripts\python.exe scripts\gen_third_party_notices.py`
> 改动依赖后必须重新生成，否则 `tests/test_notices.py` 会失败。
>
> 许可证正文在本文件同级的 `licenses/` 目录下，每个包一个子目录（保上游目录结构）。
> 其中 9 个包的 wheel 未附带正文（含 Qt/PySide6 的 LGPL），使用的是按上游发布物补充的正文，
> 来源、获取日期与 SHA-256 登记在仓库 `packaging/licenses/SOURCES.md`。

## 1. Python 包（47 个）

| 包 | 版本 | 许可证 | 许可证正文 |
| --- | --- | --- | --- |
| beautifulsoup4 | 4.15.0 | MIT License | `licenses/beautifulsoup4-4.15.0/` |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | `licenses/certifi-2026.7.22/` |
| cffi | 2.1.1 | MIT-0 | `licenses/cffi-2.1.1/` |
| charset-normalizer | 3.5.2 | MIT | `licenses/charset-normalizer-3.5.2/` |
| click | 8.5.0 | BSD-3-Clause | `licenses/click-8.5.0/` |
| cobble | 0.1.4 | BSD License | `licenses/cobble-0.1.4/` |
| coloredlogs | 15.0.1 | MIT License | `licenses/coloredlogs-15.0.1/` |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | `licenses/cryptography-50.0.2/` |
| defusedxml | 0.7.1 | Python Software Foundation License | `licenses/defusedxml-0.7.1/` |
| et_xmlfile | 2.0.0 | MIT License | `licenses/et-xmlfile-2.0.0/` |
| flatbuffers | 25.12.19 | Apache Software License | `licenses/flatbuffers-25.12.19/` |
| humanfriendly | 10.0 | MIT License | `licenses/humanfriendly-10.0/` |
| idna | 3.20 | BSD-3-Clause | `licenses/idna-3.20/` |
| lxml | 6.1.3 | BSD-3-Clause | `licenses/lxml-6.1.3/` |
| magika | 0.6.3 | Apache Software License | `licenses/magika-0.6.3/` |
| mammoth | 1.11.0 | BSD License | `licenses/mammoth-1.11.0/` |
| markdownify | 1.2.3 | MIT License | `licenses/markdownify-1.2.3/` |
| markitdown | 0.1.8 | MIT | `licenses/markitdown-0.1.8/` |
| mpmath | 1.3.0 | BSD License | `licenses/mpmath-1.3.0/` |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | `licenses/numpy-2.5.4/` |
| olefile | 0.47 | BSD License | `licenses/olefile-0.47/` |
| onnxruntime | 1.20.1 | MIT License | `licenses/onnxruntime-1.20.1/` |
| openpyxl | 3.1.5 | MIT License | `licenses/openpyxl-3.1.5/` |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | `licenses/packaging-26.3/` |
| pandas | 3.0.6 | BSD License | `licenses/pandas-3.0.6/` |
| pdfminer.six | 20260107 | MIT | `licenses/pdfminer-six-20260107/` |
| pdfplumber | 0.11.10 | MIT License | `licenses/pdfplumber-0.11.10/` |
| pillow | 12.3.0 | MIT-CMU | `licenses/pillow-12.3.0/` |
| protobuf | 7.36.2 | 3-Clause BSD License | `licenses/protobuf-7.36.2/` |
| pycparser | 3.11 | BSD-3-Clause | `licenses/pycparser-3.11/` |
| pypdfium2 | 5.14.0 | BSD-3-Clause, Apache-2.0, dependency licenses | `licenses/pypdfium2-5.14.0/` |
| pyreadline3 | 3.5.6 | BSD License | `licenses/pyreadline3-3.5.6/` |
| PySide6_Essentials | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | `licenses/pyside6-essentials-6.12.0/` |
| python-dateutil | 2.9.0.post0 | BSD License | `licenses/python-dateutil-2.9.0.post0/` |
| python-dotenv | 1.2.4 | BSD-3-Clause | `licenses/python-dotenv-1.2.4/` |
| python-pptx | 1.0.2 | MIT License | `licenses/python-pptx-1.0.2/` |
| requests | 2.34.2 | Apache Software License | `licenses/requests-2.34.2/` |
| shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | `licenses/shiboken6-6.12.0/` |
| six | 1.17.0 | MIT License | `licenses/six-1.17.0/` |
| soupsieve | 3.0.3 | MIT | `licenses/soupsieve-3.0.3/` |
| sympy | 1.14.0 | BSD License | `licenses/sympy-1.14.0/` |
| typing_extensions | 4.16.0 | PSF-2.0 | `licenses/typing-extensions-4.16.0/` |
| tzdata | 2026.5 | Apache-2.0 | `licenses/tzdata-2026.5/` |
| urllib3 | 2.8.0 | MIT | `licenses/urllib3-2.8.0/` |
| xlrd | 2.0.2 | BSD License | `licenses/xlrd-2.0.2/` |
| xlsxwriter | 3.2.9 | BSD License | `licenses/xlsxwriter-3.2.9/` |
| youtube-transcript-api | 1.2.4 | MIT License | `licenses/youtube-transcript-api-1.2.4/` |

## 2. 非 Python 组件（随包分发的原生库与运行时）

| 组件 | 版本 | 许可证 | 说明 |
| --- | --- | --- | --- |
| Qt 6 / PySide6 | 随 PySide6-Essentials 6.12.0 分发 | LGPL-3.0-only（另可选 GPL-2.0/3.0 或商业许可） | `_internal/PySide6/Qt6*.dll` 等；合规说明见第 3 节 |
| shiboken6 | 随 shiboken6 6.12.0 分发 | LGPL-3.0-only | Qt 的 Python 绑定运行时，`_internal/shiboken6/` |
| Python 3.12 | CPython 3.12（随打包运行时） | PSF-2.0 | `python312.dll`、`python3.dll` |
| Microsoft Visual C++ 运行库 / UCRT | 随 Python 3.12 与 numpy 分发 | Microsoft 可再分发条款 | `VCRUNTIME140.dll`、`VCRUNTIME140_1.dll`、`msvcp140-*.dll`、`ucrtbase.dll`、`api-ms-win-*` |
| OpenSSL | 随 cryptography 50.0.2 分发 | Apache-2.0 | `libcrypto-3-x64.dll`、`libssl-3-x64.dll` |
| libffi | 随 cffi 2.1.1 分发 | MIT | `libffi-8.dll` |
| PDFium | 随 pypdfium2 5.14.0 分发 | BSD-3-Clause | `pypdfium2_raw/pdfium.dll`；其内置第三方正文见 `licenses/pypdfium2-5.14.0/` |
| OpenBLAS（scipy-openblas64） | 随 numpy 2.5.4 分发 | BSD-3-Clause | `numpy.libs/libscipy_openblas64_*.dll` |
| ONNX Runtime（C++ 部分） | 随 onnxruntime 1.20.1 分发 | MIT | `onnxruntime/capi/onnxruntime.dll` |
| Mesa（软件 OpenGL 回退） | 随 PySide6-Essentials 6.12.0 分发 | MIT | `_internal/PySide6/opengl32sw.dll`；`MDD_TRIM=aggressive` 裁剪时会被移除 |
| PyInstaller 引导程序（bootloader） | 随 PyInstaller 6.22.3 分发 | GPL-2.0-or-later 附特别例外 | 已编入 `markitdown-desktop.exe`；例外条款明确允许分发用它打包的程序 |

## 3. Qt / PySide6 的 LGPL-3.0 合规说明

本程序用 PySide6（Qt for Python）构建界面，按 **LGPL-3.0-only** 使用
（PySide6 另提供 GPL-2.0/3.0 与商业许可，本程序不采用）。为满足 LGPL-3.0 的要求：

1. **许可证全文随包分发**：`licenses/pyside6-essentials-*/` 与 `licenses/shiboken6-*/` 内含
   LGPL-3.0 与 GPL-3.0 全文（LGPL-3.0 正文本身引用了 GPL-3.0，两者必须同时提供）。
2. **动态链接、可替换**：Qt 与 shiboken6 以独立 DLL 形式存放在安装目录的
   `_internal\PySide6\`、`_internal\shiboken6\` 下，未与本程序静态链接。你可以自行编译
   Qt/PySide6，并用你的版本替换这些 DLL 后继续使用本程序。
3. **源码获取**：Qt 源码见 <https://code.qt.io/>，PySide6 源码见
   <https://code.qt.io/cgit/pyside/pyside-setup.git/>。
4. 本程序未修改 PySide6/Qt 源码。若你自行修改了 Qt 库，需自行承担相应的开源义务。

## 4. 本程序自身

MarkItDown Desktop 采用 MIT 许可证，全文见仓库根目录的 `LICENSE`
（Copyright (c) 2026 KXW）。上游项目 markitdown 由 Microsoft 开发，同样采用 MIT。

