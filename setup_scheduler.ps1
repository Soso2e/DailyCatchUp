$pythonExe = "C:\Users\So2e\AppData\Local\Programs\Python\Python313\python.exe"
$workDir   = "C:\#Project\Server\DailyCatchUp"

# Morning (06:00) - 朝のパイプライン
$actionM  = New-ScheduledTaskAction -Execute $pythonExe -Argument "scheduler\runner.py --morning" -WorkingDirectory $workDir
$triggerM = New-ScheduledTaskTrigger -Daily -At "06:00"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 3) -StartWhenAvailable

Register-ScheduledTask `
    -TaskName    "DailyCatchUp-Morning" `
    -Action      $actionM `
    -Trigger     $triggerM `
    -Settings    $settings `
    -RunLevel    Highest `
    -Description "DailyCatchUp morning pipeline (collect, NotebookLM, Discord)" `
    -Force

Write-Host "Morning task registered: 06:00 daily" -ForegroundColor Green

# Evening (19:00) - 夜のパイプライン
$actionE  = New-ScheduledTaskAction -Execute $pythonExe -Argument "scheduler\runner.py --evening" -WorkingDirectory $workDir
$triggerE = New-ScheduledTaskTrigger -Daily -At "19:00"
$settings2 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable

Register-ScheduledTask `
    -TaskName    "DailyCatchUp-Evening" `
    -Action      $actionE `
    -Trigger     $triggerE `
    -Settings    $settings2 `
    -RunLevel    Highest `
    -Description "DailyCatchUp evening pipeline (YouTube upload, Discord night)" `
    -Force

Write-Host "Evening task registered: 19:00 daily" -ForegroundColor Green

# Discord Bot - PC起動時に常駐起動
$actionB  = New-ScheduledTaskAction -Execute $pythonExe -Argument "-m discord.bot" -WorkingDirectory $workDir
$triggerB = New-ScheduledTaskTrigger -AtLogOn
$settings3 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 365) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName    "DailyCatchUp-Bot" `
    -Action      $actionB `
    -Trigger     $triggerB `
    -Settings    $settings3 `
    -RunLevel    Highest `
    -Description "DailyCatchUp Discord Bot (slash commands, always-on)" `
    -Force

Write-Host "Discord Bot task registered: starts at login, auto-restarts on crash" -ForegroundColor Green
Write-Host ""
Write-Host "Done. Tasks are visible in Task Scheduler > Task Scheduler Library." -ForegroundColor Cyan
