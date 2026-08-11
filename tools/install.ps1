# ============================================================================
# relatima-translate · 一键安装脚本（Windows）
#
# 下载源码后运行本脚本，自动完成：
#   1. 检测/安装 Python 3.11~3.12、git、ffmpeg、uv
#   2. 创建 venv_wlk（WhisperLiveKit）与 venv_glue（glue）
#   3. 安装 PyTorch cu128 + WhisperLiveKit + 依赖（阿里云 pip 镜像）
#   4. 下载 WLK 模型（whisper small + NLLB，hf-mirror）
#   5. 克隆 IndexTTS2 + uv sync + 下载 5.5GB 模型（ModelScope 国内源）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File tools\install.ps1
#   powershell -ExecutionPolicy Bypass -File tools\install.ps1 -SkipModels   # 跳过模型下载（测试/调试）
#
# 安装完成后运行 dubbing\start_services.ps1 启动服务。
# ============================================================================
param(
    [switch]$SkipModels,
    [switch]$SkipIndexTTS
)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

$AliyunPip   = "https://mirrors.aliyun.com/pypi/simple"
$HfEndpoint  = "https://hf-mirror.com"
$PyTorchIdx  = "https://download.pytorch.org/whl/cu128"

function Log($m) { Write-Host "[$([DateTime]::Now.ToString('HH:mm:ss'))] $m" -ForegroundColor Cyan }

function Ensure-Command($name, $wingetId) {
    if (Get-Command $name -ErrorAction SilentlyContinue) { return $true }
    Log "未找到 $name，尝试 winget 安装 $wingetId ..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id $wingetId -e --accept-package-agreements --accept-source-agreements --silent | Out-Null
        # 刷新 PATH
        $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
        return [bool](Get-Command $name -ErrorAction SilentlyContinue)
    }
    Write-Host "请手动安装 $name 后重试。" -ForegroundColor Yellow
    return $false
}

function Get-PythonExe {
    # 1) py 启动器里的 3.12 / 3.11（winget 安装的 Python 会注册 py）
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($ver in @("3.12", "3.11")) {
            $out = & py "-$ver" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -Last 1) }
        }
    }
    # 2) 常见安装路径（避免触发 Windows Store 的 python 别名）
    foreach ($p in @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python312\python.exe", "C:\Python311\python.exe"
    )) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# ---------------------------------------------------------------- 1. 工具链
Log "=== 检查 Python / git / ffmpeg / uv ==="
$py = Get-PythonExe
if (-not $py) {
    Log "未找到 Python 3.11/3.12，尝试 winget 安装 Python 3.12 ..."
    if (Ensure-Command winget "Microsoft.Winget.Source") { } # 确保 winget 存在
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements --silent | Out-Null
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
    $py = Get-PythonExe
    if (-not $py) { Write-Host "Python 安装失败，请手动安装 Python 3.12 (勾选 Add to PATH)。" -ForegroundColor Red; exit 1 }
}
Log "Python: $py  ($(& $py --version))"
Ensure-Command git "Git.Git" | Out-Null
Ensure-Command ffmpeg "Gyan.FFmpeg" | Out-Null

# uv（IndexTTS2 官方要求）
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Log "安装 uv ..."
    curl.exe -LsSf https://astral.sh/uv/install.ps1 -o "$env:TEMP\install_uv.ps1"
    powershell -ExecutionPolicy Bypass -File "$env:TEMP\install_uv.ps1" | Out-Null
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Log "uv: $(uv --version)"

# ---------------------------------------------------------------- 2. venv_wlk
Log "=== 创建 venv_wlk ==="
if (-not (Test-Path "$Root\venv_wlk\Scripts\python.exe")) {
    & $py -m venv "$Root\venv_wlk" | Out-Null
}
$pw = "$Root\venv_wlk\Scripts\python.exe"

Log "=== 安装 PyTorch cu128 + WhisperLiveKit（阿里云镜像）==="
& $pw -m pip install --upgrade pip -q
$needTorch = -not (& $pw -c "import torch" 2>$null)
if ($needTorch) {
    Log "安装 torch cu128（约 2.5GB，耐心等待）..."
    & $pw -m pip install torch torchaudio --index-url $PyTorchIdx
}
if (-not (& $pw -c "import whisperlivekit" 2>$null)) {
    & $pw -m pip install numpy faster-whisper librosa soundfile uvicorn websockets huggingface-hub tqdm tiktoken python-multipart nllw fastapi --index-url $AliyunPip -q
    & $pw -m pip install whisperlivekit --no-deps --index-url $AliyunPip -q
}
& $pw -c "import torch; print('torch', torch.__version__, '| cuda:', torch.cuda.is_available())"

# ---------------------------------------------------------------- 3. WLK 模型
if (-not $SkipModels) {
    Log "=== 下载 WLK 模型（whisper small + NLLB，hf-mirror）==="
    $env:HF_ENDPOINT = $HfEndpoint
    & $pw "$Root\tools\fetch_models.py"
    $env:HF_HUB_OFFLINE = "1"
} else {
    Log "(-SkipModels) 跳过 WLK 模型下载"
}

# ---------------------------------------------------------------- 4. venv_glue
Log "=== 创建 venv_glue ==="
if (-not (Test-Path "$Root\venv_glue\Scripts\python.exe")) {
    & $py -m venv "$Root\venv_glue" | Out-Null
}
$pg = "$Root\venv_glue\Scripts\python.exe"
& $pg -m pip install --upgrade pip -q
if (-not (& $pg -c "import websockets, yaml, sounddevice, requests, numpy" 2>$null)) {
    & $pg -m pip install websockets requests sounddevice numpy pyyaml --index-url $AliyunPip -q
}
Log "glue 依赖就绪"

# ---------------------------------------------------------------- 5. IndexTTS2
if (-not $SkipIndexTTS) {
    Log "=== IndexTTS2 ==="
    if (-not (Test-Path "$Root\index-tts\pyproject.toml")) {
        Log "克隆 index-tts 仓库 ..."
        git clone --depth 1 https://github.com/index-tts/index-tts.git "$Root\index-tts"
    }
    if (-not (Test-Path "$Root\index-tts\.venv")) {
        Log "uv sync（阿里云镜像，约 2~5 分钟）..."
        Push-Location "$Root\index-tts"
        & uv sync --extra webui --default-index $AliyunPip | Out-Null
        Pop-Location
    }
    if (-not (Test-Path "$Root\index-tts\checkpoints\config.yaml")) {
        if (-not $SkipModels) {
            Log "安装 modelscope 下载工具 ..."
            & uv tool install "modelscope" | Out-Null
            Log "下载 IndexTTS-2 模型 5.5GB（ModelScope 国内源，~40MB/s）..."
            & modelscope download --model IndexTeam/IndexTTS-2 --local_dir "$Root\index-tts\checkpoints"
        }
    }
} else {
    Log "(-SkipIndexTTS) 跳过 IndexTTS2"
}

# ---------------------------------------------------------------- 6. 参考音频
if (-not (Test-Path "$Root\glue\refs\default.wav") -and (Test-Path "$Root\dubbing\samples\jfk.wav")) {
    New-Item -ItemType Directory -Path "$Root\glue\refs" -Force | Out-Null
    Copy-Item "$Root\dubbing\samples\jfk.wav" "$Root\glue\refs\default.wav"
}

# ---------------------------------------------------------------- 完成
Log ""
Log "=== 安装完成 ==="
Log "设置翻译 API key（DeepSeek）：setx DEEPSEEK_API_KEY sk-你的key"
Log "启动服务：powershell -ExecutionPolicy Bypass -File dubbing\start_services.ps1"
Log "浏览器演示见 dubbing\README.md"
