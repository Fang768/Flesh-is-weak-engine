#!/bin/bash
taskkill //F //IM QQ.exe //T 2>/dev/null | head -2
sleep 1
echo '--- start plain with --enable-logging ---'
cmd //c start "" "C:\Program Files\Tencent\QQNT\QQ.exe" --enable-logging
sleep 6
powershell -NoProfile -Command "Get-Process QQ -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count"
