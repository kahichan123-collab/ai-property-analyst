# -*- coding: utf-8 -*-
# _launch.ps1 — 顯示中文提示，等待伺服器起來後自動開瀏覽器。
# 由 start_here.cmd 呼叫（cmd.exe 用 cp950 讀 .cmd，所以中文一定要放在這裡）。
param([int]$Port = 8765, [int]$DelaySeconds = 2)

$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here

if ($DelaySeconds -gt 0) {
    Write-Host ''
    Write-Host '  AI Property Analyst' -ForegroundColor White
    Write-Host '  -------------------'
    Write-Host '  正在啟動，瀏覽器會自動開啟…'
    Write-Host ("  網址   http://127.0.0.1:{0}/" -f $Port)
    Write-Host '  登入   帳號任意（例如 owner） · 密碼 hkhouse2026'
    Write-Host ''
    Write-Host '  要停止：關閉這個視窗，或按 Ctrl+C'
    Write-Host ''
}
