; MarkItDown Desktop 安装脚本（Inno Setup 6）
;
; 设计要点（为什么这么写，改之前先读）：
; 1) PrivilegesRequired=lowest + {autopf}：per-user 安装到 %LOCALAPPDATA%\Programs，
;    双击安装全程不弹 UAC —— 这是本产品的核心体验目标，不要改成 admin。
;    保留 commandline 覆盖（/ALLUSERS）是给高级用户留口子，但界面上不给选项，
;    避免普通用户误选"为所有人安装"后又撞上 UAC。
; 2) 通告与许可证正文在安装目录根**再放一份**：PyInstaller 把非 exe 文件都塞进 _internal\，
;    用户基本不会翻到那儿；而合规要求"用户可见"。代价约 700 KB（压缩后 ~150 KB），
;    换来的是点开安装目录就能看到 THIRD-PARTY-NOTICES.md 与 licenses\。
; 3) AppVersion 不写死：由 scripts\build_installer.ps1 从 pyproject.toml 读出后经 /D 传入，
;    避免"代码 0.1.0、安装包 0.0.9"这类漂移；单独手跑 ISCC 会直接报错而不是默默编出旧版本。
; 4) [Registry] 只负责卸载时清掉 AppSettings 写的 HKCU\Software\MarkItDownDesktop；
;    dontcreatekey 表示安装时不主动建键（应用首次运行自己会建）。
; 5) 日志目录 %LOCALAPPDATA%\MarkItDownDesktop\logs 卸载时**保留**：那是用户数据/排障证据，
;    不属于"安装残留"（详见 docs/p1-verification.md 的卸载说明）。

#define AppName "MarkItDown Desktop"
#define AppExe "markitdown-desktop.exe"
#define DistDir "..\dist\markitdown-desktop"
#define RepoRoot ".."

#ifndef AppVersion
  #error 缺少 AppVersion：请用 scripts\build_installer.ps1 编译（它从 pyproject.toml 读取版本号并 /DAppVersion=... 传入）
#endif

[Setup]
; AppId 一旦发布就不能改：它是升级/卸载的识别标识（改了会变成"装了两份"）
AppId={{03988AE2-35A0-40B6-8586-818BBDCF3184}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=KXW
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayName={#AppName}
; 应用图标：与 exe 同一份，由 scripts\make_icon.py 从 packaging\icon\app-icon.svg 生成
SetupIconFile={#RepoRoot}\packaging\icon\app-icon.ico
UninstallDisplayIcon={app}\{#AppExe}
; 输出到仓库根的 dist\installer（相对路径基于本脚本所在目录）
OutputDir=..\dist\installer
OutputBaseFilename=MarkItDownDesktop-{#AppVersion}-setup
; 许可页显示 MIT 全文；通告作为"安装前信息"页显示（Markdown 原样呈现，可读）
LicenseFile={#RepoRoot}\LICENSE
InfoBeforeFile={#RepoRoot}\THIRD-PARTY-NOTICES.md
; 升级安装时关掉正在运行的实例（Restart Manager），但不自动重启，避免打断用户
CloseApplications=yes
RestartApplications=no

[Languages]
; Inno 6.7.3 自带 29 种语言但**不含中文**，所以把官方仓库（jrsoftware/issrc）的
; ChineseSimplified.isl 随仓库带上（packaging/languages/，21 KB，UTF-8）。
; 只声明一种语言 → 不弹语言选择页，少一步交互；应用界面本身也是中文。
Name: "chinese"; MessagesFile: "languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
; 主体：PyInstaller one-folder 产物（markitdown-desktop.exe + _internal\）
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 合规文件：放到安装目录根供用户直接查阅（_internal\ 里已有一份，这里是刻意复制）
Source: "{#RepoRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
; 不给 THIRD-PARTY-NOTICES.md 建快捷方式：机器上没装 Markdown 查看器时那会是个打不开的
; 空快捷方式。通告就在安装目录里，安装向导也全文展示过。

[Registry]
Root: HKCU; Subkey: "Software\MarkItDownDesktop"; Flags: uninsdeletekey dontcreatekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent
