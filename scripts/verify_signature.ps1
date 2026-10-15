<#
.SYNOPSIS
    校验 Authenticode 签名：发布链路上「签完必须回验」的那一步。

.DESCRIPTION
    对每个待校验文件调用 Get-AuthenticodeSignature，**只有 Status 为 Valid 才判通过**。

    判据来自 2026-10-14 在本机的实测（探针与原始数据见 docs/code-signing.md）：
      * 未签名                      -> Status=NotSigned、SignatureType=None；
      * 自签名（证书链不可信）       -> Status=UnknownError、SignatureType=Authenticode；
      * 把这张根证书信任后再验        -> Status=Valid；
      * **篡改一个字节后再验**        -> Status 变回 UnknownError，而 SignerCertificate **依然非空**；
      * 未签名的**非 PE** 文件可能报 NotSigned、也可能报 UnknownError（两种都实测到了），
        所以「没签」不能用某一个状态值去认，只能认 Valid 这一个正面判据。

    由此得到三条结论，也是本脚本只认 Valid 的原因：
      1) 只判「SignerCertificate 不为空」是**假验证**：被篡改的文件照样能过；
      2) 只判 Status 也不够：自签名与被篡改同为 UnknownError，分不开；
      3) 唯一能同时否掉「没签 / 被篡改 / 链不可信」的判据是 Status -eq 'Valid'，
         正式签名（证书链可信）必须走这一条。

    已知盲区（写在这里免得被误当成「验过了」）：
      * 带**目录签名**的系统文件（如 attrib.exe），Get-AuthenticodeSignature 报的是
        目录签名而不是嵌入签名，本脚本不该拿来验这类文件；
      * 本脚本不校验「有没有时间戳」，只把时间戳者打印出来供人看。

    2026-10-15 加固（起因是线上一次红灯，不是凭空的「更健壮」）：
      * 现象：tests/test_verify_signature.py 在 GitHub runner 上偶发 2 条失败
        （报告 schema 与未签名用例），报告文件**根本没生成**，测试读到 {}，
        failure 里只剩下 set() / KeyError: 'all_passed'，看不出任何原因。
      * 机制（本机已确定性复现，探针数据见 docs/p2-verification.md）：目标文件被
        另一个进程持有时，Get-AuthenticodeSignature 抛 IOException「文件正由另一
        进程使用」。脚本当时没有 try/catch，于是**在写报告之前就终止**——报告丢了，
        失败原因也一起丢了。runner 上具体是谁占用文件未证实（CI 日志域名国内不可达）。
      * 现在：单个文件取签名失败不再终止脚本，而是产出一条 Status=Error 的记录
        （Message = 异常类型 + 首行，截断 200 字符），报告照写、总体判不通过。
        「验签脚本自己挂了却看起来什么都没发生」是最坏的结果，宁可留下可读的错误。

.PARAMETER Path
    要校验的文件路径，可以传多个。

.PARAMETER JsonReport
    可选：把每条结果写成 JSON（UTF-8 **不带 BOM**），便于 CI 归档。

    报告里每个文件的字段（**只增不改**）：Path / Exists / Status / SignatureType /
    Signer / TimeStamper / Passed / Message。Status 只认 Valid 为通过，另外两个
    自定义值是 FileNotFound（路径没找到）与 Error（取签名时抛了异常）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -Command "& 'scripts\verify_signature.ps1' -Path @('dist\markitdown-desktop\markitdown-desktop.exe', 'dist\installer\MarkItDownDesktop-0.1.0-setup.exe')"

.NOTES
    @author 康小汪【kxw】
    @date 2026-10-14（初版）/ 2026-10-15（异常不再丢报告）
#>
[CmdletBinding()]
param(
    # 多文件必须按 PowerShell 的数组语义传：`-Path $arr`（CI 就是这么调的）或
    # `-Path @('a','b')`。2026-10-14 实测：`-Path a b` 与 `-File ... -Path a,b`
    # **都不行** —— 前者报「找不到接受实际参数 b 的位置形式参数」，后者把 "a,b"
    # 当成一个路径、于是报 FileNotFound，看起来像「文件没了」，很误导。
    # ValueFromRemainingArguments 试过了，救不了 `-Path a b`，所以不留它。
    [Parameter(Mandatory = $true, Position = 0)]
    [string[]] $Path,

    [string] $JsonReport
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# 结果记录统一由这里构造：字段集合永远一致（schema 只增不改），Passed 只认 Valid。
function New-SignatureRecord {
    <#
    .SYNOPSIS  构造一条校验结果记录。
    .PARAMETER Status   .NET 的枚举值，或我们自定义的 FileNotFound / Error。
    .PARAMETER Message  取签名失败时的原因（异常类型 + 首行），正常情况下为空。
    .NOTES     @author 康小汪【kxw】 @date 2026-10-15
    #>
    param(
        [string] $FilePath,
        [bool]   $Exists,
        [string] $Status,
        [string] $SignatureType = '',
        [string] $Signer = '',
        [string] $TimeStamper = '',
        [string] $Message = ''
    )

    [pscustomobject]@{
        Path          = $FilePath
        Exists        = $Exists
        Status        = $Status
        SignatureType = $SignatureType
        Signer        = $Signer
        TimeStamper   = $TimeStamper
        Passed        = ($Status -eq 'Valid')
        Message       = $Message
    }
}

# 单个文件的校验结果。Status=FileNotFound / Error 是我们自己造的状态，与 .NET 的枚举区分开。
function Get-SignatureResult {
    <#
    .SYNOPSIS  校验单个文件并返回一条结果记录。
    .PARAMETER FilePath  文件路径。
    .OUTPUTS   PSCustomObject：Path / Exists / Status / SignatureType / Signer / TimeStamper / Passed / Message
    .NOTES     @author 康小汪【kxw】 @date 2026-10-14（初版）/ 2026-10-15（异常不再丢报告）
    #>
    param([string] $FilePath)

    # 文件不存在时不要抛异常：CI 里更希望看到「哪个路径没找到」这种可读结论，而不是堆栈。
    try {
        if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
            return New-SignatureRecord -FilePath $FilePath -Exists $false -Status 'FileNotFound'
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $FilePath
    } catch {
        # 取签名抛异常（例如文件被别的进程占用 -> IOException）时也必须留下记录：
        # 否则脚本会在写报告之前终止，报告与失败原因一起丢（线上就是这么红的）。
        $firstLine = @(([string] $_.Exception.Message) -split "`r?`n")[0]
        if ($firstLine.Length -gt 200) { $firstLine = $firstLine.Substring(0, 200) }
        $reason = '{0}: {1}' -f $_.Exception.GetType().Name, $firstLine
        return New-SignatureRecord -FilePath $FilePath -Exists $true -Status 'Error' -Message $reason
    }

    $signer = if ($signature.SignerCertificate) { [string] $signature.SignerCertificate.Subject } else { '' }
    $stamper = if ($signature.TimeStamperCertificate) { [string] $signature.TimeStamperCertificate.Subject } else { '' }
    New-SignatureRecord -FilePath $FilePath -Exists $true -Status ([string] $signature.Status) `
        -SignatureType ([string] $signature.SignatureType) -Signer $signer -TimeStamper $stamper
}

# 把每条结果打到日志里：不管过没过都打全，红灯时不用再去猜是哪一项不合格。
function Show-SignatureResults {
    <#
    .SYNOPSIS  把校验结果逐条打印到控制台。
    .PARAMETER Results  Get-SignatureResult 产出的结果数组。
    .NOTES     @author 康小汪【kxw】 @date 2026-10-14
    #>
    param($Results)

    foreach ($item in $Results) {
        $mark = if ($item.Passed) { 'OK  ' } else { 'FAIL' }
        Write-Host ("{0}  {1}" -f $mark, $item.Path)
        Write-Host ("        状态={0}  类型={1}" -f $item.Status, $item.SignatureType)
        if (-not $item.Exists)    { Write-Host '        原因=文件不存在（签名无从谈起）' }
        if ($item.Message)        { Write-Host ("        异常={0}" -f $item.Message) }
        if ($item.Signer)         { Write-Host ("        签名者={0}" -f $item.Signer) }
        if ($item.TimeStamper)    { Write-Host ("        时间戳者={0}" -f $item.TimeStamper) }
    }
}

# 写 JSON 报告：显式不带 BOM，写法与版本无关（Set-Content -Encoding utf8 在 PS 5.1 上会带 BOM）。
function Save-SignatureReport {
    <#
    .SYNOPSIS  把校验结果写成 JSON 报告文件。
    .PARAMETER Results   校验结果数组。
    .PARAMETER JsonPath  报告输出路径。
    .NOTES     @author 康小汪【kxw】 @date 2026-10-14
    #>
    param($Results, [string] $JsonPath)

    $report = [ordered]@{
        tool       = 'verify_signature.ps1'
        schema     = 1
        all_passed = (@($Results | Where-Object { -not $_.Passed }).Count -eq 0)
        files      = @($Results)
    }
    $json = $report | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($JsonPath, $json, (New-Object System.Text.UTF8Encoding $false))
}

$results = @()
foreach ($item in $Path) { $results += Get-SignatureResult -FilePath $item }

Show-SignatureResults -Results $results
if ($JsonReport) { Save-SignatureReport -Results $results -JsonPath $JsonReport }

$failed = @($results | Where-Object { -not $_.Passed })
if ($failed.Count -gt 0) {
    Write-Host ("签名校验未通过：{0} / {1} 个文件不合格。" -f $failed.Count, $results.Count)
    Write-Host '未签名的产物不要挂到 Release 上；开启签名的步骤见 docs/code-signing.md。'
    exit 1
}

Write-Host ("签名校验通过：{0} 个文件状态均为 Valid。" -f $results.Count)
exit 0
