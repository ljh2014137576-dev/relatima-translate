# Start all services for real-time dubbing.
# 1) WhisperLiveKit ASR+translation (:8000)  2) IndexTTS2 dubbing (:50001)  3) glue relay (:5100)
#
# Layout expected next to this script's repo:
#   <repo>/
#     dubbing/            (this script)
#     glue/               (glue source)
#     indextts_service/   (TTS HTTP service)
#     index-tts/          (IndexTTS2 repo + checkpoints/, uv-managed)   [gitignored]
#     venv_wlk/           (WhisperLiveKit venv)                        [gitignored]
#     venv_glue/          (glue venv)                                  [gitignored]
#
# Run:  powershell -ExecutionPolicy Bypass -File dubbing/start_services.ps1
$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$env:HF_HUB_OFFLINE = "1"   # models are cached locally; skip flaky HF HEAD checks
$env:HF_ENDPOINT = "https://hf-mirror.com"

# DeepSeek key is required for LLM translation (glue will not start without it).
if (-not $env:DEEPSEEK_API_KEY) {
    Write-Host "WARNING: DEEPSEEK_API_KEY not set. Set it first:" -ForegroundColor Yellow
    Write-Host "  setx DEEPSEEK_API_KEY sk-你的key   (then reopen terminal)" -ForegroundColor Yellow
    Write-Host "  (glue 将无法启动翻译，除非 config.yaml 的 llm.api_key 已填写)" -ForegroundColor Yellow
}

if (-not (Test-Path "$Root\index-tts")) { Write-Host "ERROR: $Root\index-tts not found. See dubbing/README.md."; exit 1 }
if (-not (Test-Path "$Root\venv_wlk\Scripts\wlk.exe")) { Write-Host "ERROR: venv_wlk missing."; exit 1 }
if (-not (Test-Path "$Root\venv_glue\Scripts\python.exe")) { Write-Host "ERROR: venv_glue missing."; exit 1 }

# --- 1. WhisperLiveKit ------------------------------------------------------
Write-Host "[1/3] Starting WhisperLiveKit on :8000 ..."
$wlk = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($wlk) {
    Write-Host "  WLK already running"
} else {
    $warmup = "$Root\dubbing\samples\jfk.wav"
    $args = @("--model","small","--language","auto","--target-language","zh",
              "--nllb-size","600M","--host","127.0.0.1","--port","8000",
              "--pcm-input","--cors-origins","*")
    if (Test-Path $warmup) { $args += @("--warmup-file", $warmup) }
    Start-Process -FilePath "$Root\venv_wlk\Scripts\wlk.exe" -ArgumentList $args `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput "$Root\wlk_stdout.log" -RedirectStandardError "$Root\wlk_stderr.log" | Out-Null
}
$ok = $false
for ($i=0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 5
    try { if ((curl.exe -s -m 3 "http://127.0.0.1:8000/health") -match "ok") { Write-Host "  WLK healthy"; $ok = $true; break } } catch {}
}
if (-not $ok) { Write-Host "  WARNING: WLK not healthy yet" }

# --- 2. IndexTTS2 service ---------------------------------------------------
Write-Host "[2/3] Starting IndexTTS2 dubbing service on :50001 ..."
$tts = Get-NetTCPConnection -LocalPort 50001 -State Listen -ErrorAction SilentlyContinue
if ($tts) {
    Write-Host "  TTS already running"
} else {
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    $env:PYTHONPATH = "$Root\index-tts"
    Start-Process -FilePath "uv" -ArgumentList "run","$Root\indextts_service\server.py","--host","127.0.0.1","--port","50001" `
        -WorkingDirectory "$Root\index-tts" -WindowStyle Hidden `
        -RedirectStandardOutput "$Root\tts_stdout.log" -RedirectStandardError "$Root\tts_stderr.log" | Out-Null
}
$ok = $false
for ($i=0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 5
    try { if ((curl.exe -s -m 3 "http://127.0.0.1:50001/health") -match '"ok"') { Write-Host "  TTS healthy"; $ok = $true; break } } catch {}
}
if (-not $ok) { Write-Host "  WARNING: TTS not healthy yet (model load can take ~2min)" }

# --- 3. glue relay ----------------------------------------------------------
Write-Host "[3/3] Starting glue relay on :5100 ..."
$glue = Get-NetTCPConnection -LocalPort 5100 -State Listen -ErrorAction SilentlyContinue
if ($glue) {
    Write-Host "  glue already running"
} else {
    Start-Process -FilePath "$Root\venv_glue\Scripts\python.exe" -ArgumentList "main.py" `
        -WorkingDirectory "$Root\glue" -WindowStyle Hidden `
        -RedirectStandardOutput "$Root\glue_out.log" -RedirectStandardError "$Root\glue_err.log" | Out-Null
}
Start-Sleep -Seconds 3
Write-Host "Done. Browser demo: mute the video tab, open the extension and click Start Capture."
