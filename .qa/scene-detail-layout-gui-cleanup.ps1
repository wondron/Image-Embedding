# QA ONLY. Stop layout-handoff PIDs only if command lines still match.
$p = Get-CimInstance Win32_Process -Filter "ProcessId=53836"
if ($p -and $p.CommandLine.Replace('\','/') -like '*C:/Users/47202/PROJECT/Image-Embedding/frontend/node_modules/vite/bin/vite.js*') { Stop-Process -Id 53836 -ErrorAction Stop } elseif ($p) { Write-Warning 'frontend PID command line changed; NOT stopped' }
$p = Get-CimInstance Win32_Process -Filter "ProcessId=46208"
if ($p -and $p.CommandLine -like '*scene_detail_layout_gui_qa.py*') { Stop-Process -Id 46208 -ErrorAction Stop } elseif ($p) { Write-Warning 'API PID command line changed; NOT stopped' }
