# 补充许可证正文（构建输入，不是生成物）

`THIRD-PARTY-NOTICES.md` 与仓库根的 `licenses/` 是 `scripts/gen_third_party_notices.py`
**生成**的；本目录是它的**输入**：存放那些「上游 wheel 里没带许可证正文」的包的许可证全文。

为什么需要这个目录：`gen_third_party_notices.py` 只能从 `*.dist-info` 里拷贝上游附带的正文。
实测发现运行时依赖里有 8 个包不带正文（见下表），其中 `PySide6-Essentials` / `shiboken6`
是 **LGPL-3.0**——LGPL 明确要求随二进制分发许可证全文，所以必须补。

本目录内容已入库，**构建不需要联网**。修改任何文件后必须同步更新下表（`tests/test_notices.py`
会校验 SHA-256，防止许可证正文被无声改动）。

| 文件 | 对应包 | 上游项目 | 来源 URL | 获取日期 | SHA-256 |
|------|--------|----------|----------|----------|---------|
| `LGPL-3.0.txt` | `PySide6-Essentials`、`shiboken6` | GNU 项目 | https://www.gnu.org/licenses/lgpl-3.0.txt | 2026-10-13 | `e3a994d82e644b03a792a930f574002658412f62407f5fee083f2555c5f23118` |
| `GPL-3.0.txt` | `PySide6-Essentials`、`shiboken6` | GNU 项目 | https://www.gnu.org/licenses/gpl-3.0.txt | 2026-10-13 | `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986` |
| `markitdown.txt` | `markitdown` | microsoft/markitdown | 本地 upstream 仓库 `LICENSE`（0.1.8 的 sdist 未附正文） | 2026-10-13 | `9906940f61b1f0b533fa7d99baf55178b2808fbe113ea51dfbfad8572ccd5f2b` |
| `magika.txt` | `magika` | google/magika | https://cdn.jsdelivr.net/gh/google/magika@main/LICENSE | 2026-10-13 | `58d1e17ffe5109a7ae296caafcadfdbe6a7d176f0bc4ab01e12a689b0499d8bd` |
| `onnxruntime.txt` | `onnxruntime` | microsoft/onnxruntime | https://cdn.jsdelivr.net/gh/microsoft/onnxruntime@main/LICENSE | 2026-10-13 | `2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c` |
| `flatbuffers.txt` | `flatbuffers` | google/flatbuffers | https://cdn.jsdelivr.net/gh/google/flatbuffers@master/LICENSE | 2026-10-13 | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `openpyxl.txt` | `openpyxl` | openpyxl | PyPI sdist `openpyxl-3.1.5.tar.gz` 内的 `LICENCE.rst` | 2026-10-13 | `0c84bb42f5d367e5ebf9fc2dde35b16141df5ee0fdc189250858bc6c5560f69e` |
| `et-xmlfile.txt` | `et-xmlfile` | et_xmlfile | PyPI sdist `et_xmlfile-2.0.0.tar.gz` 内的 `LICENCE.rst` | 2026-10-13 | `0c84bb42f5d367e5ebf9fc2dde35b16141df5ee0fdc189250858bc6c5560f69e` |
| `et-xmlfile-python.txt` | `et-xmlfile` | et_xmlfile | PyPI sdist `et_xmlfile-2.0.0.tar.gz` 内的 `LICENCE.python` | 2026-10-13 | `4ccdaaebc0f44b83720ec0399bb7ab329adce067dd62f73c5535a2dc05628ab2` |
| `cobble.txt` | `cobble` | mwilliamson/python-cobble | **维护者依据项目声明整理**（见文件头说明；上游仓库无许可证文件） | 2026-10-13 | `45bf100864a2883732b866bd5ccc7e64c061f3a242e5d36197f32563a650e4a4` |
| `inter-OFL.txt` | **Inter 字体**（`packaging/fonts/` 里的 4 个字重，T29 起随包分发）| rsms/inter（The Inter Project Authors）| https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip 内的 `LICENSE.txt` | 2026-10-15 | `262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a` |

## 注意事项

1. `LGPL-3.0.txt` 的正文本身引用了 GPL-3.0（LGPL-3.0 第 3 条），所以两个文件必须一起分发。
2. `et-xmlfile.txt` 与 `openpyxl.txt` 内容相同（都出自 openpyxl 项目），SHA-256 一致属预期。
3. `cobble.txt` 是唯一「非上游原文」的文件，已在文件头写明依据；如果将来上游补上许可证文件，
   应改为直接引用上游原文。
4. 取件时 `raw.githubusercontent.com` 不可达，故用 jsDelivr 镜像与 PyPI sdist 作为来源；
   PyPI sdist 与所安装版本一一对应，是更可靠的来源。
5. `inter-OFL.txt` 不是 Python 包，而是**随包分发的字体**：Inter 采 SIL Open Font License 1.1，
   允许嵌入与再分发（含商用），要求保留版权声明与许可证全文 —— 本目录与
   `THIRD-PARTY-NOTICES.md` 就是为了满足这条。字体文件本体在 `packaging/fonts/`（4 个静态字重）。
