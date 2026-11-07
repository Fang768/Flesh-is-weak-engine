# Enable WSL2 Windows features (ASCII only, UTF-8 BOM on disk)
$ErrorActionPreference = "Continue"
$log = "D:\Code\Synthetic_Dawn\nexus-bot\logs\enable_features.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Log([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log ("admin=" + $isAdmin)
Log ("windows=" + [System.Environment]::OSVersion.Version.ToString())

$features = @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")
foreach ($f in $features) {
    Log ("enabling " + $f)
    $out = & dism.exe /online /enable-feature /featurename:$f /all /norestart 2>&1
    foreach ($line in $out) { Log ("  " + ("" + $line).Trim()) }
    Log ("  exit=" + $LASTEXITCODE)
}
foreach ($f in $features) {
    $state = "unknown"
    try { $state = (Get-WindowsOptionalFeature -Online -FeatureName $f).State } catch { $state = "query-failed" }
    Log ("state " + $f + " = " + $state)
}
Log "done (reboot required)"
Write-Host "Done. Press Enter to close."
