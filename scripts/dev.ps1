# 一键启动：首次运行会自动安装依赖。后端 http://127.0.0.1:8400 ，前端 http://localhost:3400
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

if (-not (Test-Path (Join-Path $backend ".venv"))) {
    Write-Host "== 安装后端依赖 ==" -ForegroundColor Cyan
    Push-Location $backend
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv .venv --python 3.11
        uv pip install -p .venv -r requirements.txt
    } else {
        python -m venv .venv
        & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    }
    Pop-Location
}
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "== 安装前端依赖 ==" -ForegroundColor Cyan
    Push-Location $frontend
    npm install
    Pop-Location
}

Write-Host "== 启动后端 (8400) ==" -ForegroundColor Cyan
$py = Join-Path $backend ".venv\Scripts\python.exe"
$be = Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8400", "--reload" `
    -WorkingDirectory $backend -PassThru -NoNewWindow

try {
    Write-Host "== 启动前端 (3400) ==  浏览器打开 http://localhost:3400" -ForegroundColor Green
    Push-Location $frontend
    npm run dev
} finally {
    Pop-Location
    if ($be -and -not $be.HasExited) { Stop-Process -Id $be.Id -Force }
}
