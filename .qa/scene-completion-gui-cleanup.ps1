# QA ONLY. Exact immutable container IDs. Stop PIDs only if their command lines still match own QA.
$p = Get-CimInstance Win32_Process -Filter "ProcessId=47568"
if ($p -and $p.CommandLine.Replace('\','/') -like '*C:/Users/47202/PROJECT/Image-Embedding/frontend/node_modules/vite/bin/vite.js*') { Stop-Process -Id 47568 -ErrorAction Stop } elseif ($p) { Write-Warning 'PID47568 command line changed; NOT stopped' }
$p = Get-CimInstance Win32_Process -Filter "ProcessId=16532"
if ($p -and $p.CommandLine.Replace('\','/') -like '*scene_completion_gui_qa.py*') { Stop-Process -Id 16532 -ErrorAction Stop } elseif ($p) { Write-Warning 'PID16532 command line changed; NOT stopped' }
docker stop 797c6a3c97e166e9ad5c939c24e3bca885b355490f8a71793f1efc423176eda4 ad801c0769beada83f230dd38013d4b691249262692238756fb586aa470d8aeb
docker rm 797c6a3c97e166e9ad5c939c24e3bca885b355490f8a71793f1efc423176eda4 ad801c0769beada83f230dd38013d4b691249262692238756fb586aa470d8aeb
