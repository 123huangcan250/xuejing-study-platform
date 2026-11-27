# 本机日常使用：生产模式启动（前端内存约 80 MB，开发模式要 1.6 GB 左右）。
# 只有改代码、需要热更新时才用 dev.ps1。
#
#   .\scripts\run.ps1            启动（第一次或前端代码改过后会先构建）
#   .\scripts\run.ps1 -Rebuild   强制重新构建前端
#
# 前后端都只监听 127.0.0.1；浏览器打开 http://localhost:3400 。按 Ctrl+C 停止。
# 需要从外面访问请用 remote.ps1（它在此基础上加 Tailscale 和防休眠）。
param([switch]$Rebuild)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
# 生产构建放在单独的 .next-prod（与 remote.ps1 共用）：开发服务器（dev.ps1 / 预览）会改写 .next，
# 共用同一个目录时，开发服务器一启动就会弄坏生产构建。
$env:NEXT_DIST_DIR = ".next-prod"

function Fail($msg) { Write-Host "`n$msg" -ForegroundColor Red; exit 1 }
function Port-InUse($port) { return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) }

if (-not (Test-Path $py)) { Fail "还没有安装后端依赖，请先运行一次 dev.ps1。" }
if ((Port-InUse 3400) -or (Port-InUse 8400)) { Fail "3400 或 8400 端口被占用（dev.ps1 / remote.ps1 还开着？）。请先关闭它们再运行。" }

if ($Rebuild -or -not (Test-Path (Join-Path $frontend ".next-prod\BUILD_ID"))) {
    Write-Host "== 构建前端（生产模式，输出到 frontend\.next-prod，只需要在前端代码改过后做一次）==" -ForegroundColor Cyan
    Push-Location $frontend; try { npm run build; if ($LASTEXITCODE -ne 0) { Fail "前端构建失败。" } } finally { Pop-Location }
}

$procs = @()
try {
    Write-Host "== 启动后端与前端（仅监听 127.0.0.1）==" -ForegroundColor Cyan
    $procs += Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8400" -WorkingDirectory $backend -PassThru -NoNewWindow
    $env:BACKEND_URL = "http://127.0.0.1:8400"  # 前端中间件在运行时用它转发 /api
    $procs += Start-Process -FilePath "npm.cmd" -ArgumentList "run", "start" -WorkingDirectory $frontend -PassThru -NoNewWindow
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-RestMethod "http://127.0.0.1:8400/api/health" -TimeoutSec 2 | Out-Null; Invoke-WebRequest "http://127.0.0.1:3400/" -TimeoutSec 3 -UseBasicParsing | Out-Null; break } catch { Start-Sleep 1 }
        if ($i -eq 59) { Fail "服务没有在 60 秒内启动成功。" }
    }
    Write-Host "`n已启动：http://localhost:3400  （按 Ctrl+C 停止）`n" -ForegroundColor Green
    while ($true) { Start-Sleep 5; if ($procs | Where-Object { $_.HasExited }) { Fail "有服务意外退出了。" } }
} finally {
    foreach ($p in $procs) { if ($p -and -not $p.HasExited) { Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
    Write-Host "已停止。"
}
