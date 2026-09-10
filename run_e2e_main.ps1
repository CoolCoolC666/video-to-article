# run_e2e_main.ps1 - 启动主程序 0.6B 端到端测试
$ErrorActionPreference = 'Stop'
Set-Location 'E:\000~\YilanChengWen-src'
& '.\.venv\Scripts\Activate.ps1'

$env:HF_HOME = 'E:\AI_Models\Qwen3-ASR'
$env:HF_ENDPOINT = 'https://hf-mirror.com'

$mp3 = 'E:\000~\YilanChengWen-0.4.5\data\bilibili\Bilibili-甘泽成谣雨成诗\audio\差分宇宙科研前台符玄震撼发布连试用不死途也能够保住的顶级生存位优化后的缇宝主C全程思路让你看完轻松连_BV1Wd4X6fEnZ.mp3'
$log = 'E:\000~\YilanChengWen-src\run_e2e_main.log'

Write-Host "=== 启动主程序 0.6B 端到端 ==="
Write-Host "mp3: $mp3"
Write-Host "prompt: general_article"
Write-Host "log: $log"

try {
    python -m video_to_article --local $mp3 --prompt general_article --asr-engine qwen_asr 2>&1 | Tee-Object -FilePath $log
    Write-Host "=== 主程序完成 ==="
} catch {
    Write-Host "=== 主程序失败: $_ ==="
    exit 1
}
