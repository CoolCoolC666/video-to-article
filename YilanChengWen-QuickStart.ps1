# YilanChengWen-QuickStart.ps1 - ASCII quick start
# Double-click -> file picker GUI -> pick mp3 -> run

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$fileDialog = New-Object System.Windows.Forms.OpenFileDialog
$fileDialog.Title = "YilanChengWen - select mp3"
$fileDialog.Filter = "MP3 audio (*.mp3)|*.mp3|All audio (*.mp3;*.m4a;*.wav;*.flac)|*.mp3;*.m4a;*.wav;*.flac|All files (*.*)|*.*"
$fileDialog.InitialDirectory = "E:\000~\YilanChengWen-0.4.5\data\bilibili"
$fileDialog.Multiselect = $false

$result = $fileDialog.ShowDialog()
if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    Write-Host "Cancelled" -ForegroundColor Yellow
    exit 0
}

$mp3Path = $fileDialog.FileName
Write-Host "Selected: $mp3Path" -ForegroundColor Cyan
Write-Host ""

$skillScript = "E:\AI Agent\Data list\.minimax\skills\yilan-chengwen-asr\scripts\run_yilan_chengwen.ps1"
if (-not (Test-Path -LiteralPath $skillScript)) {
    Write-Host "ERROR: skill script not found" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

& $skillScript -Mp3Path $mp3Path

Write-Host ""
Write-Host "Done. Press Enter to close" -ForegroundColor Green
Read-Host
