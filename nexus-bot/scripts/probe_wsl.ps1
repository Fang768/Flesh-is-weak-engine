$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('wsl.exe','powershell.exe','dism.exe') }
foreach ($p in $procs) {
    "PID={0} NAME={1}" -f $p.ProcessId, $p.Name
    "  CMD={0}" -f $p.CommandLine
}
"--- optional features ---"
foreach ($f in @('Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform','Microsoft-Hyper-V-All','Containers')) {
    $s = (Get-WindowsOptionalFeature -Online -FeatureName $f).State
    "{0} = {1}" -f $f, $s
}
