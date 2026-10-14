<#
.SYNOPSIS
    一键构建 MarkItDown Desktop 安装包：PyInstaller one-folder → Inno Setup → 体积检查。

.DESCRIPTION
    为什么要这么写：
    1) 版本号从 pyproject.toml 读，不写死在 installer.iss 里 —— 避免"代码 0.1.0、安装包 0.0.9"漂移；
       ISCC 用 /DAppVersion=... 传入，installer.iss 里没传就直接 #error 报错。
    2) 编译前先断言通告/许可证已生成：缺文件时 Inno 的报错信息很难懂（只说 Source 不存在），
       这里给一句人话，并提示去跑 gen_third_party_notices.py。
    3) 体积阈值 75 MB：超出即失败，防止"悄悄胖起来"。

.PARAMETER SkipPyInstaller
    跳过 PyInstaller，直接用现有 dist\ 目录编译安装包（改 .iss 时迭代用，省 2.5 分钟）。
    风险自负：dist\ 可能是旧的，脚本会打印它的时间戳让你自己判断。

.EXAMPLE
    powershell -File scripts\build_installer.ps1
    powershell -File scripts\build_installer.ps1 -SkipPyInstaller

@author 康小汪【kxw】
@date 2026-10-13
#>
[CmdletBinding()]
param(
    [switch]$SkipPyInstaller,
    [int]$SizeLimitMb = 75
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "未找到虚拟环境 Python：$python" }

# Inno Setup：优先 winget 的 per-user 安装位置，再退回机器级安装位置
$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "未找到 ISCC.exe。请先安装 Inno Setup 6：winget install -e --id JRSoftware.InnoSetup"
}

# 从 pyproject.toml 取版本号（唯一真源；installer.iss 不允许自己写版本）
$pyproject = Get-Content (Join-Path $root "pyproject.toml") -Raw
if ($pyproject -notmatch '(?m)^version\s*=\s*"([^"]+)"') { throw "pyproject.toml 里读不到 version" }
$appVersion = $Matches[1]

# 编译前必须存在的合规文件（T10 产出）
foreach ($required in @("THIRD-PARTY-NOTICES.md", "LICENSE", "licenses")) {
    if (-not (Test-Path (Join-Path $root $required))) {
        throw "缺少 $required。请先运行：$python scripts\gen_third_party_notices.py"
    }
}

$distExe = Join-Path $root "dist\markitdown-desktop\markitdown-desktop.exe"
if ($SkipPyInstaller) {
    if (-not (Test-Path $distExe)) { throw "dist\ 里没有 markitdown-desktop.exe，无法跳过 PyInstaller" }
    $stamp = (Get-Item $distExe).LastWriteTime
    Write-Host "[1/3] 跳过 PyInstaller，使用现有 dist\（构建时间：$stamp）"
} else {
    Write-Host "[1/3] PyInstaller 打包（MDD_TRIM=safe）…"
    $env:MDD_TRIM = "safe"
    & $python -m PyInstaller --noconfirm --clean packaging\markitdown-desktop.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（退出码 $LASTEXITCODE）" }
}

Write-Host "[2/3] Inno Setup 编译安装包（版本 $appVersion）…"
& $iscc "/DAppVersion=$appVersion" "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC 失败（退出码 $LASTEXITCODE）" }

Write-Host "[3/3] 体积检查…"
$setup = Get-ChildItem "dist\installer\*-setup.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $setup) { throw "dist\installer\ 下没有生成安装包" }
$distBytes = (Get-ChildItem "dist\markitdown-desktop" -Recurse -File | Measure-Object Length -Sum).Sum
$setupMb = [math]::Round($setup.Length / 1MB, 2)

$report = [ordered]@{
    app_version    = $appVersion
    setup_path     = $setup.FullName
    setup_bytes    = $setup.Length
    setup_mb       = $setupMb
    onedir_mb      = [math]::Round($distBytes / 1MB, 1)
    size_limit_mb  = $SizeLimitMb
    built_at       = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    inno_setup     = $iscc
    skip_pyinstaller = [bool]$SkipPyInstaller
}
$reportPath = Join-Path $root "dist\installer\build-report.json"
$report | ConvertTo-Json | Set-Content -Path $reportPath -Encoding UTF8

Write-Host ("one-folder : {0} MB" -f $report.onedir_mb)
Write-Host ("installer  : {0} MB -> {1}" -f $setupMb, $setup.FullName)
Write-Host ("report     : {0}" -f $reportPath)
if ($setupMb -gt $SizeLimitMb) { throw "安装包超过 $SizeLimitMb MB 阈值（$setupMb MB）" }
Write-Host "PASS"
