# 远程访问：在宿舍电脑上以生产模式运行，并通过 Tailscale Serve 只对你自己的 Tailscale 网络开放。
#
#   .\scripts\remote.ps1            启动（首次会引导设置访问密码）
#   .\scripts\remote.ps1 -Stop      停止并撤销 Tailscale Serve
#   .\scripts\remote.ps1 -SkipBuild 跳过前端构建（代码没改时更快）
#
# 安全模型：前端和后端都只监听本机（127.0.0.1），不对局域网开放；唯一的远程入口是 Tailscale Serve
# （只有登录同一个 Tailscale 账号的设备能连，并且走 HTTPS）；没有设置访问密码时本脚本拒绝启动远程入口；
# 访问密码只能在本机设置。资料、密钥、清华登录状态都留在这台电脑上，远程设备只看到页面。
param([switch]$Stop, [switch]$SkipBuild)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
# 生产构建放在单独的 .next-prod：开发服务器（dev.ps1 / 预览）会改写 .next，
# 曾把正在运行的生产服务的 routes-manifest 覆盖掉导致其崩溃、远程入口被一并关闭（2026-11-20）。
$env:NEXT_DIST_DIR = ".next-prod"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$logFile = Join-Path $backend "data\remote.log"  # 记录何时启动/停止及原因（不含任何密码或 token）
function Log($msg) { try { Add-Content -Path $logFile -Value ("{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg) -Encoding UTF8 } catch {} }

function Fail($msg) { Log "失败：$msg"; Write-Host "`n$msg" -ForegroundColor Red; exit 1 }
function Find-Tailscale {
    $c = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @("C:\Program Files\Tailscale\tailscale.exe", "C:\Program Files (x86)\Tailscale\tailscale.exe")) { if (Test-Path $p) { return $p } }
    return $null
}
function Port-InUse($port) { return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) }

$ts = Find-Tailscale
if ($Stop) {
    if ($ts) { & $ts serve reset | Out-Null; Write-Host "已撤销 Tailscale Serve（远程入口已关闭）。" -ForegroundColor Green }
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "app\.main:app" -or $_.CommandLine -match "next(\.cmd)? start" -or $_.CommandLine -match "next[\\/]dist.*start" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "已停止本程序的前后端服务。"
    exit 0
}

# 1. Tailscale
if (-not $ts) { Fail "没有找到 Tailscale。请先安装并登录：https://tailscale.com/download （宿舍电脑和你远程用的设备要登录同一个 Tailscale 账号）" }
& $ts status *> $null
if ($LASTEXITCODE -ne 0) { Fail "Tailscale 还没有登录或没有运行。请打开 Tailscale 并登录后重试。" }

# 2. 访问密码（没有密码绝不开放远程入口）
Push-Location $backend
try {
    & $py -m app.access_cli status
    if ($LASTEXITCODE -eq 3) {
        Write-Host "还没有设置访问密码。远程访问必须先设置（在本窗口输入，不会显示，也不会保存明文）。" -ForegroundColor Yellow
        & $py -m app.access_cli set
        if ($LASTEXITCODE -ne 0) { Fail "访问密码没有设置成功，已取消。" }
    }
} finally { Pop-Location }

# 3. 端口与构建
if ((Port-InUse 3400) -or (Port-InUse 8400)) { Fail "3400 或 8400 端口被占用（可能 dev.ps1 还开着）。请先关闭它们再运行。" }
if ($SkipBuild -and -not (Test-Path (Join-Path $frontend ".next-prod\BUILD_ID"))) {
    Write-Host "还没有 .next-prod 生产构建，忽略 -SkipBuild。" -ForegroundColor Yellow
    $SkipBuild = $false
}
if (-not $SkipBuild) {
    Write-Host "== 构建前端（生产模式，输出到 frontend\.next-prod）==" -ForegroundColor Cyan
    Push-Location $frontend; try { npm run build; if ($LASTEXITCODE -ne 0) { Fail "前端构建失败。" } } finally { Pop-Location }
}

# 4. 防休眠（只在本脚本运行期间生效，不修改系统电源设置）
Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED = 0x80000001。PowerShell 会把 0x8... 的十六进制字面量当成负数，所以用十进制
[void][Win32.Power]::SetThreadExecutionState([uint32]2147483649)

$procs = @()
try {
    Write-Host "== 启动后端与前端（仅监听 127.0.0.1）==" -ForegroundColor Cyan
    $procs += Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8400" -WorkingDirectory $backend -PassThru -NoNewWindow
    $env:BACKEND_URL = "http://127.0.0.1:8400"  # 前端中间件在运行时用它转发 /api
    Push-Location $frontend
    $procs += Start-Process -FilePath "npm.cmd" -ArgumentList "run", "start" -WorkingDirectory $frontend -PassThru -NoNewWindow
    Pop-Location
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-RestMethod "http://127.0.0.1:8400/api/health" -TimeoutSec 2 | Out-Null; Invoke-WebRequest "http://127.0.0.1:3400/" -TimeoutSec 3 -UseBasicParsing | Out-Null; break } catch { Start-Sleep 1 }
        if ($i -eq 59) { Fail "服务没有在 60 秒内启动成功。" }
    }

    # 5. Tailscale Serve：把 https://<本机名>.<tailnet>.ts.net 转发到本机 3400（只对你的 tailnet 可见）
    Write-Host "== 开启 Tailscale Serve ==" -ForegroundColor Cyan
    $out = & $ts serve --bg 3400 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host $out
        Fail "Tailscale Serve 没能启动。常见原因：账号还没在管理后台启用 HTTPS 证书（https://login.tailscale.com/admin/dns 里打开 HTTPS Certificates）。"
    }
    $dns = ((& $ts status --json | ConvertFrom-Json).Self.DNSName).TrimEnd(".")
    Write-Host "`n远程访问地址：https://$dns/" -ForegroundColor Green
    Write-Host "在图书馆的设备上登录同一个 Tailscale 账号，打开上面的地址，输入访问密码即可。"
    Write-Host "按 Ctrl+C 停止，并自动关闭远程入口。`n"

    # 清华 token 剩余时间提醒：人在外面时二次验证窗口只会弹在这台电脑上
    Push-Location $backend; try { & $py -m app.tsinghua_cli status } catch {} finally { Pop-Location }
    Write-Host "（若清华 token 快到期，出门前请先在本机更新一次；到期更新需要二次验证时，验证窗口只会出现在这台电脑上。）" -ForegroundColor Yellow

    Log "已启动：https://$dns/"
    $names = @("后端", "前端")
    while ($true) {
        Start-Sleep 5
        for ($i = 0; $i -lt $procs.Count; $i++) {
            if ($procs[$i].HasExited) { Fail "$($names[$i])服务意外退出了（退出码 $($procs[$i].ExitCode)）。可查看本窗口上方的报错；若是前端，常见原因是生产构建损坏，去掉 -SkipBuild 重新运行即可。" }
        }
    }
} finally {
    if ($ts) { & $ts serve reset *> $null }
    foreach ($p in $procs) { if ($p -and -not $p.HasExited) { Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
    [void][Win32.Power]::SetThreadExecutionState([uint32]2147483648)  # ES_CONTINUOUS：恢复系统默认的休眠策略
    Log "已关闭远程入口并停止服务"
    Write-Host "已关闭远程入口并停止服务。"
}
