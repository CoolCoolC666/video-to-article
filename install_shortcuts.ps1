# install_shortcuts.ps1 - Build Windows shortcuts for YilanChengWen quick start
# Usage: pwsh E:\000~\YilanChengWen-src\install_shortcuts.ps1
# 100% ASCII paths to avoid PS 5.1 GBK issues

$ErrorActionPreference = "Stop"

$src = "E:\000~\YilanChengWen-src"
$quickStartGui = Join-Path $src "YilanChengWen-QuickStart.ps1"
$skillScript = "E:\AI Agent\Data list\.minimax\skills\yilan-chengwen-asr\scripts\run_yilan_chengwen.ps1"
$runGuiBat = Join-Path $src "run_gui.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$sendto = Join-Path $env:APPDATA "Microsoft\Windows\SendTo"

$psExe = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path $psExe)) {
    $psExe = (Get-Command powershell.exe).Source
}

function New-Lnk {
    param($LnkPath, $Target, $Arguments, $IconPath, $Description)
    $ws = New-Object -ComObject WScript.Shell
    $shortcut = $ws.CreateShortcut($LnkPath)
    $shortcut.TargetPath = $Target
    $shortcut.Arguments = $Arguments
    if ($IconPath) { $shortcut.IconLocation = $IconPath }
    if ($Description) { $shortcut.Description = $Description }
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    Write-Host ("Created: " + $LnkPath) -ForegroundColor Green
}

Write-Host "=== YilanChengWen quick-start shortcut installer ===" -ForegroundColor Cyan
Write-Host ""

# 1. Desktop - GUI file picker (CLI mode with OpenFileDialog)
$lnk1 = Join-Path $desktop "YilanChengWen-QuickStart.lnk"
$arg1 = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"" + $quickStartGui + "`""
New-Lnk -LnkPath $lnk1 -Target $psExe -Arguments $arg1 -IconPath "$env:WINDIR\System32\shell32.dll,12" -Description "YilanChengWen CLI - double click to pick mp3"

# 2. SendTo menu - right-click mp3
$lnk2 = Join-Path $sendto "YilanChengWen-Transcribe.lnk"
$arg2 = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"" + $skillScript + "`" -Mp3Path `"%1`""
New-Lnk -LnkPath $lnk2 -Target $psExe -Arguments $arg2 -IconPath "$env:WINDIR\System32\shell32.dll,12" -Description "Right-click mp3 -> Send to -> YilanChengWen"

# 3. Desktop - drag-drop mode
$lnk3 = Join-Path $desktop "YilanChengWen-DropHere.lnk"
$arg3 = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"" + $skillScript + "`" -Mp3Path"
New-Lnk -LnkPath $lnk3 -Target $psExe -Arguments $arg3 -IconPath "$env:WINDIR\System32\shell32.dll,12" -Description "Drop mp3 file on this icon to transcribe"

# 4. Desktop - GUI mode (PyQt Fluent, run_gui.bat)
if (Test-Path -LiteralPath $runGuiBat) {
    $lnk4 = Join-Path $desktop "YilanChengWen-GUI.lnk"
    New-Lnk -LnkPath $lnk4 -Target $runGuiBat -Arguments "" -IconPath (Join-Path $src "src\video_to_article\gui\resources\app.ico") -Description "YilanChengWen PyQt GUI - click to launch"
} else {
    Write-Host "WARN: run_gui.bat not found, GUI shortcut skipped" -ForegroundColor Yellow
}

# 5. Start menu shortcut (optional - add to Start Menu for Windows search)
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
if ((Test-Path -LiteralPath $startMenu) -and (Test-Path -LiteralPath $runGuiBat)) {
    $lnk5 = Join-Path $startMenu "YilanChengWen-GUI.lnk"
    $targetExe = (Get-Command cmd.exe).Source
    $arg5 = "/c `"" + $runGuiBat + "`""
    New-Lnk -LnkPath $lnk5 -Target $targetExe -Arguments $arg5 -IconPath (Join-Path $src "src\video_to_article\gui\resources\app.ico") -Description "YilanChengWen PyQt GUI"
}

Write-Host ""
Write-Host "=== Installation complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "4 quick-start methods:" -ForegroundColor Cyan
Write-Host "  1. Desktop [YilanChengWen-QuickStart.lnk] - CLI + GUI file picker"
Write-Host "  2. Desktop [YilanChengWen-DropHere.lnk] - drag mp3 onto icon"
Write-Host "  3. Desktop [YilanChengWen-GUI.lnk] - PyQt Fluent GUI (full)"
Write-Host "  4. Explorer right-click mp3 -> Send to -> YilanChengWen-Transcribe"
Write-Host ""
Write-Host "Uninstall: delete the .lnk files" -ForegroundColor Yellow
