# 安装 WSL2 + Ubuntu 24.04（需要管理员）
# 只做系统级安装，不改现有 QQ / SnowLuma / NoneBot 任何文件。
$ErrorActionPreference = "Continue"
$log = "D:\Code\Synthetic_Dawn\nexus-bot\logs\wsl_install.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}

Log "=== 开始安装 WSL2 ==="
Log ("管理员: " + ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
Log ("Windows: " + (Get-CimInstance Win32_OperatingSystem).Version)

# 1) 功能开关
$feat1 = "(Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux).State"
$feat2 = "(Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform).State"
Log ("WSL 功能: " + (Invoke-Expression $feat1))
Log ("VM 平台: " + (Invoke-Expression $feat2))

# 2) 官方安装命令（带日志）
Log "运行: wsl --install --no-launch -d Ubuntu-24.04"
$out = & wsl.exe --install --no-launch -d Ubuntu-24.04 2>&1
$code = $LASTEXITCODE
$out | ForEach-Object { Log ("wsl> " + $_) }

if ($code -ne 0) {
    Log "wsl --install 退出码 $code，回退到 dism 手动启用"
    & dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart 2>&1 | ForEach-Object { Log ("dism> " + $_) }
    & dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart 2>&1 | ForEach-Object { Log ("dism> " + $_) }
    Log "dism 完成，需要重启后再执行 wsl --install"
}

# 3) 状态回显
Log "--- wsl --status ---"
(& wsl.exe --status 2>&1) | ForEach-Object { Log ("status> " + $_) }
Log "--- wsl -l -v ---"
(& wsl.exe -l -v 2>&1) | ForEach-Object { Log ("list> " + $_) }
Log "=== 结束 ==="
