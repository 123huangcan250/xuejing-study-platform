# 清华大模型服务本机设置。账号密码在本终端无回显输入，由当前 Windows 用户 DPAPI 加密保存。
#   .\scripts\tsinghua-setup.ps1            设置 / 更新账号密码（改密码后也用它）
#   .\scripts\tsinghua-setup.ps1 login      打开浏览器窗口亲自登录（验证码 / 二次验证）
#   .\scripts\tsinghua-setup.ps1 status     查看状态（不显示 token）
#   .\scripts\tsinghua-setup.ps1 clear      删除本机账号密码
#   .\scripts\tsinghua-setup.ps1 logout     删除 token 与浏览器登录状态
param([string]$Command = "setup")
$backend = Join-Path (Split-Path -Parent $PSScriptRoot) "backend"
Push-Location $backend
try { & .\.venv\Scripts\python.exe -m app.tsinghua_cli $Command } finally { Pop-Location }
