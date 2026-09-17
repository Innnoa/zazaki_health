# health_service.ps1 - controllable start/stop/restart/status for the HealthReader services.
#
#   Usage:
#     powershell -ExecutionPolicy Bypass -File health_service.ps1 <start|stop|restart|status> [all|dashboard|receiver]
#
#   Defaults: action = status, target = all.
#   No autostart is installed - these services run only while started from here.
param(
    [Parameter(Position = 0)][ValidateSet('start', 'stop', 'restart', 'status')][string]$Action = 'status',
    [Parameter(Position = 1)][ValidateSet('all', 'dashboard', 'receiver')][string]$Target = 'all'
)

$ErrorActionPreference = 'Stop'

# ---- config -----------------------------------------------------------------
$Python     = 'C:\Users\hzj\AppData\Local\Python\bin\python.exe'
$DataDir    = 'C:\Users\hzj\zazaki_health_receiver\data'

$DashDir    = 'C:\Users\hzj\zazaki_health\dashboard'
$DashScript = Join-Path $DashDir 'dashboard.py'
$DashPort   = 8890
$DashOut    = Join-Path $DashDir 'dash_out.log'
$DashErr    = Join-Path $DashDir 'dash_err.log'

$RecvDir    = 'C:\Users\hzj\zazaki_health_receiver'
$RecvScript = Join-Path $RecvDir 'phone_http_receiver_tcp.ps1'
$RecvPort   = 8899
$RecvOut    = Join-Path $RecvDir 'recv_out.log'
$RecvErr    = Join-Path $RecvDir 'recv_err.log'

$Services = @{
    dashboard = @{ Name = 'dashboard'; Port = $DashPort; Out = $DashOut; Err = $DashErr }
    receiver  = @{ Name = 'receiver';  Port = $RecvPort; Out = $RecvOut; Err = $RecvErr }
}

# ---- helpers ----------------------------------------------------------------
function Get-ListenProcId([int]$Port) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
         Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess }
    return $null
}

function Get-SvcState($svc) {
    $procId = Get-ListenProcId $svc.Port
    if ($procId) { return [pscustomobject]@{ State = 'running'; ProcId = $procId } }
    return [pscustomobject]@{ State = 'stopped'; ProcId = $null }
}

function Start-Svc($svc) {
    $st = Get-SvcState $svc
    if ($st.State -eq 'running') {
        Write-Host ("  {0,-10} already running (pid {1}, port {2})" -f $svc.Name, $st.ProcId, $svc.Port)
        return
    }
    # Launch DETACHED via WMI (Win32_Process.Create). Start-Process children keep
    # the WSL interop handles alive, which makes the caller (e.g. `health start`
    # from WSL) hang until the service exits; WMI-created processes are not
    # parented to the caller and return immediately.
    # The WMI-created cmd.exe would get its own visible console, so route the
    # launch through launch_hidden.vbs (WScript.Shell.Run with window style 0 =
    # hidden); wscript.exe is a GUI-subsystem binary, so nothing flashes.
    # (All configured paths are space-free, so cmd /c needs no inner quoting.)
    switch ($svc.Name) {
        'dashboard' {
            $inner = '{0} -u {1} --data-dir {2} --port {3}' -f $Python, $DashScript, $DataDir, $svc.Port
        }
        'receiver' {
            $inner = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File {0} -Port {1} -ListenIp 0.0.0.0' -f $RecvScript, $svc.Port
        }
    }
    $cmd = 'cmd.exe /c ' + $inner + ' >> ' + $svc.Out + ' 2>&1'
    $launcher = Join-Path $PSScriptRoot 'launch_hidden.vbs'
    $line = 'wscript.exe //nologo "' + $launcher + '" "' + $cmd + '"'
    Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $line } | Out-Null
    $deadline = (Get-Date).AddSeconds(20)
    $st = Get-SvcState $svc
    while ($st.State -ne 'running' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
        $st = Get-SvcState $svc
    }
    if ($st.State -eq 'running') {
        Write-Host ("  {0,-10} started (pid {1}, port {2})" -f $svc.Name, $st.ProcId, $svc.Port)
    } else {
        Write-Host ("  {0,-10} FAILED to start (port {1} not listening after 20s) - see {2}" -f $svc.Name, $svc.Port, $svc.Err)
    }
}

function Stop-Svc($svc) {
    $st = Get-SvcState $svc
    if ($st.State -ne 'running') {
        Write-Host ("  {0,-10} already stopped" -f $svc.Name)
        return
    }
    try {
        Stop-Process -Id $st.ProcId -Force -ErrorAction Stop
    } catch {
        Write-Host ("  {0,-10} stop failed: {1}" -f $svc.Name, $_.Exception.Message)
        return
    }
    Start-Sleep -Milliseconds 700
    $st2 = Get-SvcState $svc
    if ($st2.State -eq 'stopped') {
        Write-Host ("  {0,-10} stopped (was pid {1}, port {2})" -f $svc.Name, $st.ProcId, $svc.Port)
    } else {
        Write-Host ("  {0,-10} still listening on port {1}" -f $svc.Name, $svc.Port)
    }
}

function Show-Status($svc) {
    $st = Get-SvcState $svc
    if ($st.State -eq 'running') {
        Write-Host ("  {0,-10} RUNNING  port {1}  pid {2}" -f $svc.Name, $svc.Port, $st.ProcId)
    } else {
        Write-Host ("  {0,-10} stopped  port {1}" -f $svc.Name, $svc.Port)
    }
}

# ---- main -------------------------------------------------------------------
$targets = if ($Target -eq 'all') { @('dashboard', 'receiver') } else { @($Target) }

Write-Host ("health_service: {0} [{1}]" -f $Action, $Target)
foreach ($t in $targets) {
    $svc = $Services[$t]
    switch ($Action) {
        'start'   { Start-Svc $svc }
        'stop'    { Stop-Svc $svc }
        'restart' { Stop-Svc $svc; Start-Svc $svc }
        'status'  { Show-Status $svc }
    }
}
exit 0
