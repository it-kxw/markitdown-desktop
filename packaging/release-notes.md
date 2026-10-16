## MarkItDown Desktop {{TAG}}

把文档转成 Markdown 的 Windows 桌面版：拖进去 → 选输出目录 → 转完。
转换内核是 [microsoft/markitdown](https://github.com/microsoft/markitdown)。

### 下载哪个

| 文件 | 大小 | 说明 |
| --- | --- | --- |
| `{{SETUP_NAME}}` | {{SETUP_MB}} MB | **推荐**。安装到本机，带开始菜单项与卸载程序 |
| `{{PORTABLE_NAME}}` | {{PORTABLE_MB}} MB | 免安装。解压后双击 `markitdown-desktop.exe` |

### 校验下载是否完整

```powershell
Get-FileHash .\{{SETUP_NAME}} -Algorithm SHA256
Get-FileHash .\{{PORTABLE_NAME}} -Algorithm SHA256
```

应该得到：

| 文件 | SHA-256 |
| --- | --- |
| `{{SETUP_NAME}}` | `{{SETUP_SHA256}}` |
| `{{PORTABLE_NAME}}` | `{{PORTABLE_SHA256}}` |

### 本版更新（0.1.2）

界面做了一次大的现代化改造，功能一个没少、体积还在闸门内：

- **全新外观**：品牌蓝主题、顶部动作条（「开始转换」成为唯一主按钮）、
  队列条目带文件类型图标与状态配色、空状态引导卡、预览区头部条
- **深色模式**：跟随系统 / 浅色 / 深色三态可切，设置里「外观」一项；
  深色为**纯黑系**（近黑 `#0A0A0A`），正文对比度 16.9:1
- **界面字体**：随包分发 [Inter](https://rsms.me/inter/)（SIL OFL 1.1，已子集化到 0.52 MB）
- **可访问性**：全部正文与状态色对比度 ≥4.5:1（最低 4.77:1），有机械断言守着
- **小改进**：切换界面语言时提示需要重启（并给出「立即重启」按钮）；
  更新检查、队列图标、预览复制等交互保持一致

> 安装包与免安装版都未做代码签名，SHA-256 见上（可用于确认文件未被改动）。

### 请先看这三条

1. **安装包没有做代码签名**（证书待定），Windows SmartScreen 可能提示
   「Windows 已保护你的电脑」。点「更多信息 → 仍要运行」即可；
   上面那两行 SHA-256 也能确认文件没被动过。
2. 免安装版解压后直接运行；首次启动会解压运行时，比之后每次启动慢一些。
3. 转换在后台进行、可随时取消；输出目录里已有同名文件时不会覆盖，会另存为
   `xxx (1).md`、`xxx (2).md`。

### 本次提交

- 完整提交历史：https://github.com/it-kxw/markitdown-desktop/commits/{{TAG}}

### 许可与第三方

本项目以 MIT 许可发布；随附的 `THIRD-PARTY-NOTICES.md` 列出了所有第三方组件及其许可证。

---

说明：上面的体积与 SHA-256 都来自**真实产物**，不是手写的。
