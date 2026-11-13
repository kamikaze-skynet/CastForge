# ===================== CastForge uninstaller =====================
# Removes the CastForge folder (engine, app, models, Python), the desktop icon,
# and - if you choose - the tools CastForge setup installed for you
# (only the ones it installed itself; anything you already had is left alone).
$ErrorActionPreference = 'Continue'
trap {
    Write-Host "`nUNINSTALL ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "At: $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    Read-Host 'Press Enter to close'
    exit 1
}
$ProgressPreference = 'SilentlyContinue'
try { $Host.UI.RawUI.WindowTitle = 'CastForge Uninstall'; $Host.UI.RawUI.BackgroundColor = 'Black'; $Host.UI.RawUI.ForegroundColor = 'Gray'; Clear-Host } catch {}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

$Self = $env:CF_SELF
$Hint = Join-Path $env:TEMP 'castforge-uninstall-root.txt'

# Run from a temp copy, so the CastForge folder (where this file usually lives) can be deleted.
if ($Self -and $env:CF_ARG -ne 'fromtemp') {
    Set-Content -Path $Hint -Value (Split-Path $Self) -Encoding ASCII
    $tmp = Join-Path $env:TEMP 'CastForge-Uninstall.bat'
    Copy-Item $Self $tmp -Force
    $cmdline = "`$env:CF_SELF='$tmp'; `$env:CF_ARG='fromtemp'; iex ([IO.File]::ReadAllText(`$env:CF_SELF))"
    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "`"$cmdline`"")
    if (Test-Admin) { Start-Process -FilePath powershell.exe -ArgumentList $psArgs } else { Start-Process -FilePath powershell.exe -ArgumentList $psArgs -Verb RunAs }
    exit
}
if (-not (Test-Admin)) {
    if ($Self) {
        $cmdline = "`$env:CF_SELF='$Self'; `$env:CF_ARG='fromtemp'; iex ([IO.File]::ReadAllText(`$env:CF_SELF))"
        Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "`"$cmdline`"")
    }
    else { Write-Host 'Please run this from an Administrator PowerShell.' -ForegroundColor Red; Read-Host 'Press Enter' }
    exit
}

# ---------------------------------------------------------------- find CastForge
function Is-Root($p) { $p -and (Test-Path (Join-Path $p 'app\castforge_engine.py')) }
$Root = $null
if (Test-Path $Hint) { $h = (Get-Content $Hint -Raw).Trim(); if (Is-Root $h) { $Root = $h } }
if (-not $Root) {
    foreach ($d in (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')) {
        $p = "$($d.DeviceID)\CastForge"
        if (Is-Root $p) { $Root = $p; break }
    }
}

Write-Host ''
Write-Host '=================== CastForge Uninstall ===================' -ForegroundColor DarkYellow
if (-not $Root) {
    Warn 'No CastForge folder found (looked for <drive>:\CastForge on every drive).'
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'CastForge.lnk'
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Ok 'Removed the desktop icon.' }
    Read-Host 'Press Enter to close'
    exit
}

$Manifest = Join-Path $Root 'installed-by-castforge.txt'
$Items = @()
if (Test-Path $Manifest) { $Items = @(Get-Content $Manifest | Where-Object { $_.Trim() }) }
$Names = @{
    'winget:7zip.7zip' = '7-Zip'
    'winget:BlenderFoundation.Blender' = 'Blender'
    'winget:Microsoft.VisualStudio.2022.BuildTools' = 'Visual Studio 2022 C++ Build Tools'
    'cuda:12.9' = 'NVIDIA CUDA 12.9 toolkit (your graphics driver is NOT removed)'
}
$Tools = @($Items | Where-Object { $Names.ContainsKey($_) })

$sizeGB = 0
try { $sizeGB = [math]::Round(((Get-ChildItem $Root -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1GB, 1) } catch {}
Write-Host "Folder:  $Root  ($sizeGB GB - engine, app, AI models, Python)"
Write-Host 'Also:    the CastForge desktop icon'

$Outputs = Join-Path $Root 'app\outputs'
$hasOutputs = (Test-Path $Outputs) -and (Get-ChildItem $Outputs -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
$Keep = $false
$KeepDest = "$($Root.Substring(0,2))\CastForge-models"
if ($hasOutputs) {
    $a = Read-Host "`nKeep the 3D models you made? They'll be moved to $KeepDest (Y/n)"
    $Keep = ($a -notmatch '^[nN]')
}
$RemoveTools = $false
if ($Tools.Count) {
    Write-Host "`nSetup also installed these for you:"
    $Tools | ForEach-Object { Write-Host "  - $($Names[$_])" }
    $a = Read-Host 'Uninstall them too? (y/N)'
    $RemoveTools = ($a -match '^[yY]')
}
$a = Read-Host "`nType YES to uninstall CastForge"
if ($a -cne 'YES') { Write-Host 'Cancelled - nothing was changed.' -ForegroundColor Yellow; Read-Host 'Press Enter to close'; exit }

# ---------------------------------------------------------------- remove
Say 'Stopping CastForge'
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like "$Root\*" } | ForEach-Object {
    Ok "stopping $($_.ProcessName) ($($_.Id))"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

if ($Keep) {
    Say 'Saving your models'
    $dest = $KeepDest
    if (Test-Path $dest) { $dest = "$KeepDest-$(Get-Date -Format yyyyMMdd-HHmmss)" }
    Move-Item -Path $Outputs -Destination $dest -Force
    if (Test-Path $dest) { Ok "moved to $dest" } else { Warn 'could not move them - stopping so nothing is lost.'; Read-Host 'Press Enter'; exit 1 }
}

Say 'Removing shortcuts and extra files'
foreach ($f in ($Items | Where-Object { $_ -like 'file:*' })) {
    $p = $f.Substring(5)
    if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue; Ok "removed $p" }
}
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'CastForge.lnk'
if (Test-Path $lnk) { Remove-Item $lnk -Force }

Say "Deleting $Root (can take a minute)"
Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path $Root) { & cmd.exe /c "rd /s /q `"$Root`"" }
if (Test-Path $Root) { Warn "Some files are still in use - restart Windows, then delete $Root by hand." } else { Ok 'deleted' }

if ($RemoveTools) {
    Say 'Uninstalling tools setup installed'
    foreach ($t in $Tools) {
        if ($t -like 'winget:*') {
            $id = $t.Substring(7)
            Write-Host "    $($Names[$t]) ..."
            & winget uninstall -e --id $id --silent --accept-source-agreements --disable-interactivity | Out-Null
            if ($LASTEXITCODE -eq 0) { Ok 'removed' } else { Warn "winget couldn't remove it (code $LASTEXITCODE) - use Settings > Apps." }
        }
        elseif ($t -eq 'cuda:12.9') {
            Write-Host "    $($Names[$t]) ..."
            $keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
            $pkgs = Get-ItemProperty $keys -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like 'NVIDIA CUDA*' -and $_.DisplayName -like '*12.9*' -and $_.UninstallString }
            foreach ($c in $pkgs) {
                $u = $c.UninstallString
                if ($u -match '(?i)msiexec') {
                    $code = ($u -replace '(?i).*?/[IX]', '').Trim()
                    Start-Process msiexec.exe -ArgumentList "/x $code /qn /norestart" -Wait
                } else {
                    Start-Process cmd.exe -ArgumentList "/c `"$u -silent`"" -Wait -WindowStyle Hidden
                }
                Ok "removed $($c.DisplayName)"
            }
            if (-not $pkgs) { Ok 'already gone' }
        }
    }
}

Remove-Item $Hint -Force -ErrorAction SilentlyContinue
Write-Host ''
Write-Host '=================== CastForge removed ===================' -ForegroundColor Green
if ($Keep) { Write-Host " Your models are in $dest" }
Read-Host 'Press Enter to close'
Start-Process cmd.exe -ArgumentList "/c ping -n 3 127.0.0.1 >nul & del `"$env:TEMP\CastForge-Uninstall.bat`"" -WindowStyle Hidden
