<#
.SYNOPSIS
    构建免安装 zip：PyInstaller one-folder -> 补合规文件 -> 压成 zip -> 体积检查。

.DESCRIPTION
    为什么这么打包：
    1) 版本号从 pyproject.toml 读，不写死 —— 与 build_installer.ps1 同一口径；
    2) 压缩方式只用 Compress-Archive（Deflate）。探针实测（2026-10-14）one-folder
       211.5 MB：Deflate(9) = 92.2 MB、LZMA2 = 65.0 MB。看着 LZMA 更香，但 Windows
       自带的解压引擎（资源管理器 / Expand-Archive / .NET ZipFile）读不了 LZMA 条目，
       报「存档条目是使用不受支持的压缩方法压缩的」——双击解不开的免安装包没有意义。
       所以阈值定 100 MB 换「双击可用」，而不是死守 70 MB。
    3) zip 里只放一个顶层文件夹 MarkItDownDesktop-<版本>\：解压出来是一个干净的文件夹，
       而不是一堆文件散在桌面上；合规文件放在这个文件夹根、与 exe 同级。
    4) 暂存目录先删再建：上一次构建的残留文件不能混进这一次的包。

.PARAMETER SkipPyInstaller
    跳过 PyInstaller，直接复用现有 dist\ 目录（迭代打包脚本时省几分钟）。
    脚本会打印 dist\ 的时间戳，判断它是不是旧的是你的责任。

.PARAMETER SizeLimitMb
    免安装 zip 的体积上限（MB），默认 100。超过就抛错，不产出"看着成功"的包。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
    powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -SkipPyInstaller

@author 康小汪【kxw】
@date 2026-10-14
#>
[CmdletBinding()]
param(
    [switch]$SkipPyInstaller,
    [int]$SizeLimitMb = 100
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "未找到虚拟环境 Python：$python" }

# 版本号唯一真源：pyproject.toml
$pyproject = Get-Content (Join-Path $root "pyproject.toml") -Raw
if ($pyproject -notmatch '(?m)^version\s*=\s*"([^"]+)"') { throw "pyproject.toml 里读不到 version" }
$appVersion = $Matches[1]

# 合规文件（T10 产出）：缺一个都不能发版
foreach ($required in @("LICENSE", "THIRD-PARTY-NOTICES.md", "licenses")) {
    if (-not (Test-Path (Join-Path $root $required))) {
        throw "缺少 $required。请先运行：$python scripts\gen_third_party_notices.py"
    }
}

$distDir = Join-Path $root "dist\markitdown-desktop"
$distExe = Join-Path $distDir "markitdown-desktop.exe"
if ($SkipPyInstaller) {
    if (-not (Test-Path $distExe)) { throw "dist\ 里没有 markitdown-desktop.exe，无法跳过 PyInstaller" }
    Write-Host ("[1/4] 跳过 PyInstaller，复用现有 dist\（exe 时间：{0}）" -f (Get-Item $distExe).LastWriteTime)
} else {
    Write-Host "[1/4] PyInstaller 打包（MDD_TRIM=safe）…"
    $env:MDD_TRIM = "safe"
    & $python -m PyInstaller --noconfirm --clean packaging\markitdown-desktop.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（退出码 $LASTEXITCODE）" }
}

$portableDir = Join-Path $root "dist\portable"
$stage = Join-Path $portableDir "MarkItDownDesktop-$appVersion"
$zipPath = Join-Path $portableDir "MarkItDownDesktop-$appVersion-portable.zip"

Write-Host "[2/4] 暂存到 $stage …"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Item -Path (Join-Path $distDir "*") -Destination $stage -Recurse -Force
foreach ($extra in @("LICENSE", "THIRD-PARTY-NOTICES.md")) {
    Copy-Item -Path (Join-Path $root $extra) -Destination $stage -Force
}
Copy-Item -Path (Join-Path $root "licenses") -Destination $stage -Recurse -Force

Write-Host "[3/4] 压缩（Deflate：资源管理器可直接解压；LZMA 能更小但解不开）…"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path $stage -DestinationPath $zipPath -CompressionLevel Optimal
if (-not (Test-Path $zipPath)) { throw "没有生成 $zipPath" }

Write-Host "[4/4] 体积检查…"
$zipBytes = (Get-Item $zipPath).Length
$zipMb = [math]::Round($zipBytes / 1MB, 2)
$onedirMb = [math]::Round((Get-ChildItem $distDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)

$report = [ordered]@{
    app_version      = $appVersion
    zip_path         = $zipPath
    zip_bytes        = $zipBytes
    zip_mb           = $zipMb
    onedir_mb        = $onedirMb
    size_limit_mb    = $SizeLimitMb
    compression      = "Deflate (Compress-Archive Optimal)；LZMA2 可压到 65 MB 但资源管理器解不开"
    top_level        = "MarkItDownDesktop-$appVersion"
    built_at         = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    skip_pyinstaller = [bool]$SkipPyInstaller
}
$reportPath = Join-Path $portableDir "build-report.json"
$report | ConvertTo-Json | Set-Content -Path $reportPath -Encoding UTF8

Write-Host ("one-folder : {0} MB" -f $onedirMb)
Write-Host ("portable   : {0} MB -> {1}" -f $zipMb, $zipPath)
Write-Host ("report     : {0}" -f $reportPath)
if ($zipMb -gt $SizeLimitMb) { throw "免安装 zip 超过 $SizeLimitMb MB 阈值（$zipMb MB）" }
Write-Host "PASS"
