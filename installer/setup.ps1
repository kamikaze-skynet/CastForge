# ===================== CastForge one-click setup =====================
# Installs everything CastForge needs into F:\CastForge (C:\CastForge if there is no F: drive)
# and puts a CastForge icon on the desktop. Uninstall: F:\CastForge\Uninstall CastForge.bat
#   7-Zip, Blender, VS C++ Build Tools, CUDA 12.9 (compiler only),
#   Hunyuan3D-2 WinPortable (3D engine), uv + Python 3.11 + CastForge app.
# Safe to re-run: finished steps are skipped.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
trap {
    Write-Host "`nSETUP CRASHED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "At: $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    Write-Host 'Send this text to Claude.' -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host 'Press Enter to close'
    exit 1
}
try { $Host.UI.RawUI.WindowTitle = 'CastForge Setup'; $Host.UI.RawUI.BackgroundColor = 'Black'; $Host.UI.RawUI.ForegroundColor = 'Gray'; Clear-Host } catch {}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-Admin)) {
    Write-Host 'CastForge setup needs admin rights (for CUDA and the C++ build tools). Approve the Windows prompt...'
    if ($env:CF_SELF) {
        $cmdline = "`$env:CF_SELF='$($env:CF_SELF)'; iex ([IO.File]::ReadAllText(`$env:CF_SELF))"
        Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "`"$cmdline`"")
    }
    else { Write-Host 'Please re-run this from an Administrator PowerShell.' -ForegroundColor Red; Read-Host 'Press Enter' }
    exit
}

$script:Log = $null
function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }
function Fail($m) {
    Write-Host "`nSETUP STOPPED: $m" -ForegroundColor Red
    if ($script:Log) { Write-Host "Full log: $script:Log" }
    Write-Host 'Fix that and double-click the setup again - finished steps are skipped.'
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host 'Press Enter to close'
    exit 1
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Get-File($url, $out, [long]$size = 0) {
    if ($size -gt 0 -and (Test-Path $out) -and ((Get-Item $out).Length -eq $size)) { Ok "already downloaded: $(Split-Path $out -Leaf)"; return }
    Write-Host "    downloading $(Split-Path $out -Leaf) ..."
    if ($size -gt 0) { & curl.exe -L -A 'Mozilla/5.0 CastForgeSetup' --fail --retry 10 --retry-delay 5 -C - -o $out $url }
    else { & curl.exe -L -A 'Mozilla/5.0 CastForgeSetup' --fail --retry 10 --retry-delay 5 -o $out $url }
    if ($LASTEXITCODE -ne 0) { Fail "Download failed ($LASTEXITCODE): $url" }
    if ($size -gt 0 -and (Get-Item $out).Length -ne $size) { Fail "Download incomplete: $out - run setup again to resume." }
}
function WinGet-Install($id, [string[]]$extra = @()) {
    Write-Host "    installing $id (winget) ..."
    & winget install -e --id $id --accept-package-agreements --accept-source-agreements --disable-interactivity @extra
    Refresh-Path
}
function Find-Blender {
    $c = New-Object System.Collections.Generic.List[string]
    Get-ChildItem "$env:ProgramFiles\Blender Foundation\Blender*\blender.exe", "${env:ProgramFiles(x86)}\Blender Foundation\Blender*\blender.exe" -ErrorAction SilentlyContinue |
        ForEach-Object { $c.Add($_.FullName) }
    $keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like 'Blender*' } | ForEach-Object {
        if ($_.InstallLocation) { $c.Add((Join-Path $_.InstallLocation 'blender.exe')) }
        if ($_.DisplayIcon) { $c.Add(($_.DisplayIcon -replace '^"|"?(,\d+)?$', '')) }
    }
    foreach ($k in 'Registry::HKEY_CLASSES_ROOT\blendfile\shell\open\command', 'Registry::HKEY_CLASSES_ROOT\Applications\blender.exe\shell\open\command') {
        try { $v = (Get-ItemProperty $k -ErrorAction Stop).'(default)'; if ($v -match '"?([^"]*blender[^"]*\.exe)') { $c.Add($matches[1]) } } catch {}
    }
    $c.Add("${env:ProgramFiles(x86)}\Steam\steamapps\common\Blender\blender.exe")
    foreach ($d in (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction SilentlyContinue)) {
        $c.Add("$($d.DeviceID)\SteamLibrary\steamapps\common\Blender\blender.exe")
        $c.Add("$($d.DeviceID)\Steam\steamapps\common\Blender\blender.exe")
        Get-ChildItem "$($d.DeviceID)\Program Files\Blender Foundation\Blender*\blender.exe" -ErrorAction SilentlyContinue | ForEach-Object { $c.Add($_.FullName) }
    }
    $w = Get-Command blender.exe -ErrorAction SilentlyContinue
    if ($w) { $c.Add($w.Source) }
    $found = $c | Where-Object { $_ -and $_ -like '*blender.exe' -and (Test-Path -LiteralPath $_) } | Select-Object -Unique
    if ($found) { return ($found | Sort-Object { (Get-Item -LiteralPath $_).VersionInfo.FileVersion } | Select-Object -Last 1) }
    return $null
}

# ---------------------------------------------------------------- GPU
Say 'Checking your graphics card'
$smi = (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($smi) { $smi = $smi.Source } elseif (Test-Path "$env:WINDIR\System32\nvidia-smi.exe") { $smi = "$env:WINDIR\System32\nvidia-smi.exe" }
else { Fail 'No NVIDIA driver found. CastForge needs an NVIDIA RTX card with its driver installed.' }
$g = ((& $smi '--query-gpu=name,memory.total,driver_version' '--format=csv,noheader,nounits') | Select-Object -First 1).Split(',') | ForEach-Object { $_.Trim() }
$GpuName = $g[0]; $VramMB = [int]$g[1]; $Driver = $g[2]
$Cap = ''
try { $Cap = ((& $smi '--query-gpu=compute_cap' '--format=csv,noheader') | Select-Object -First 1).Trim() } catch {}
$RamGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
Ok "$GpuName - $([math]::Round($VramMB / 1024)) GB VRAM - driver $Driver - $RamGB GB RAM"
if ($VramMB -lt 7500) { Warn 'Under 8 GB VRAM: shapes should work, textures may run out of memory.' }
if ($RamGB -lt 24) { Warn 'Under 24 GB RAM: the engine will use its lowest-memory mode (slower).' }
$MmgpProfile = 4
if ($VramMB -lt 11000 -or $RamGB -lt 24) { $MmgpProfile = 5 }
$NeedDriver = $false
try { $NeedDriver = ([version]$Driver -lt [version]'576.57') } catch {}

# ---------------------------------------------------------------- where to install
$Need = 60GB
$Root = 'C:\CastForge'
if (Test-Path 'F:\') { $Root = 'F:\CastForge' }
elseif (-not (Test-Path "$Root\app")) {
    $disks = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3'
    $c = $disks | Where-Object { $_.DeviceID -eq 'C:' }
    if ($c.FreeSpace -lt $Need) {
        $best = $disks | Where-Object { $_.FreeSpace -gt $Need } | Sort-Object FreeSpace -Descending | Select-Object -First 1
        if ($best) { $Root = "$($best.DeviceID)\CastForge" }
        else { Warn 'Less than 60 GB free on every drive - setup may run out of space.' }
    }
}
$free = (Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($Root.Substring(0,2))'").FreeSpace
if ($free -and $free -lt $Need -and -not (Test-Path "$Root\app")) {
    Warn "Only $([math]::Round($free / 1GB)) GB free on $($Root.Substring(0,2)) - CastForge needs about 60 GB."
}
New-Item -ItemType Directory -Force -Path $Root, "$Root\downloads", "$Root\tools" | Out-Null
$Dl = "$Root\downloads"
# keep every temp/cache file on the install drive, never C:
New-Item -ItemType Directory -Force -Path "$Root\tmp", "$Root\tools\pip-cache" | Out-Null
$env:TEMP = "$Root\tmp"; $env:TMP = "$Root\tmp"
$env:PIP_CACHE_DIR = "$Root\tools\pip-cache"
$env:UV_CACHE_DIR = "$Root\tools\uv-cache"; $env:UV_PYTHON_INSTALL_DIR = "$Root\tools\python"
$env:HF_HOME = "$Root\tools\hf-home"
$script:Log = "$Root\castforge-setup.log"
try { Start-Transcript -Path $script:Log -Append | Out-Null } catch {}
$Manifest = "$Root\installed-by-castforge.txt"
function Remember($what) {
    $have = @()
    if (Test-Path -LiteralPath $Manifest) { $have = @(Get-Content -LiteralPath $Manifest) }
    if ($have -notcontains $what) { Add-Content -LiteralPath $Manifest -Value $what }
}
Say "Installing to $Root"

# ---------------------------------------------------------------- winget tools
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail 'winget is missing. Install "App Installer" from the Microsoft Store, then run setup again.'
}

Say '7-Zip'
$7z = "$env:ProgramFiles\7-Zip\7z.exe"
if (-not (Test-Path $7z)) { WinGet-Install '7zip.7zip' @('--silent'); if (Test-Path $7z) { Remember 'winget:7zip.7zip' } }
if (-not (Test-Path $7z)) { Fail '7-Zip did not install.' }
Ok 'ready'

Say 'Blender'
$PortableBlender = "$Root\tools\blender-5.2.3-windows-x64\blender.exe"
$Blender = $null
if (Test-Path $PortableBlender) { $Blender = $PortableBlender } else { $Blender = Find-Blender }
if (-not $Blender) {
    Write-Host '    no working Blender found - downloading a portable copy into the CastForge folder (about 400 MB) ...'
    $bz = "$Dl\blender-5.2.3-windows-x64.zip"
    $mirrors = @('https://download.blender.org/release', 'https://mirrors.ocf.berkeley.edu/blender/release',
                 'https://ftp.nluug.nl/pub/graphics/blender/release', 'https://mirror.clarkson.edu/blender/release')
    foreach ($m in $mirrors) {
        if ((Test-Path $bz) -and (Get-Item $bz).Length -eq 405839452) { break }
        & curl.exe -L -A 'Mozilla/5.0 CastForgeSetup' --fail --retry 3 -C - -o $bz "$m/Blender5.2/blender-5.2.3-windows-x64.zip"
    }
    if (-not ((Test-Path $bz) -and (Get-Item $bz).Length -eq 405839452)) { Fail 'Could not download Blender from any mirror.' }
    & $7z x $bz "-o$Root\tools" -y | Out-Null
    Remove-Item $bz -ErrorAction SilentlyContinue
    if (Test-Path $PortableBlender) { $Blender = $PortableBlender }
}
if (-not $Blender) { Fail 'Blender could not be set up.' }
Ok $Blender

Say 'Visual Studio C++ build tools (for compiling the texture module)'
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
function Find-VC {
    if (-not (Test-Path $vswhere)) { return $null }
    $p = & $vswhere -products * -version '[17.0,18.0)' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($p) { return ($p | Select-Object -First 1) }
    return $null
}
$VS = Find-VC
if (-not $VS) {
    WinGet-Install 'Microsoft.VisualStudio.2022.BuildTools' @('--override', '--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended')
    $VS = Find-VC
    if ($VS) { Remember 'winget:Microsoft.VisualStudio.2022.BuildTools' }
}
if (-not $VS -and (Test-Path $vswhere)) {
    $bt = & $vswhere -products Microsoft.VisualStudio.Product.BuildTools -version '[17.0,18.0)' -property installationPath | Select-Object -First 1
    if ($bt) {
        Write-Host '    adding the C++ workload to your existing Build Tools ...'
        Start-Process -Wait -FilePath "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\setup.exe" `
            -ArgumentList @('modify', '--installPath', "`"$bt`"", '--add', 'Microsoft.VisualStudio.Workload.VCTools', '--includeRecommended', '--quiet', '--norestart')
        $VS = Find-VC
    }
}
$VcVars = $null
if ($VS) { $VcVars = Join-Path $VS 'VC\Auxiliary\Build\vcvars64.bat'; Ok $VS }
else { Warn 'C++ build tools not found - textures will be skipped (shapes still work).' }

Say "CUDA 12.9 compiler (portable, into $Root\tools\cuda - about 2.2 GB)"
$CudaHome = "$Root\tools\cuda"
if (-not (Test-Path "$CudaHome\bin\nvcc.exe") -or -not (Test-Path "$CudaHome\.complete")) {
    $redist = 'https://developer.download.nvidia.com/compute/cuda/redist'
    $parts = @(
        @('cuda_nvcc/windows-x86_64/cuda_nvcc-windows-x86_64-12.9.86-archive.zip', '227b109663b5e57d2718bcabb24a4ba0d9d4e52d958e327dc476f7c28691be85'),
        @('cuda_cudart/windows-x86_64/cuda_cudart-windows-x86_64-12.9.79-archive.zip', '179e9c43b0735ffe67207b3da556eb5a0c50f3047961882b7657d3b822d34ef8'),
        @('cuda_nvrtc/windows-x86_64/cuda_nvrtc-windows-x86_64-12.9.86-archive.zip', '1aa0644fa53c8ca34cdc73db17bcc73530557bdd3f582c7bfdbd7916c8b48f65'),
        @('libcublas/windows-x86_64/libcublas-windows-x86_64-12.9.1.4-archive.zip', 'd534d98b0b453a98914dbf3adf47d7e84b55037abf02f87466439e1dcef581ed'),
        @('libcusparse/windows-x86_64/libcusparse-windows-x86_64-12.5.10.65-archive.zip', 'abb4bfc01198f82fbc956773ccb98c578c03027b6ad425e829355be0c0a11a4a'),
        @('libcusolver/windows-x86_64/libcusolver-windows-x86_64-11.7.5.82-archive.zip', 'e991d64a0bbe3e0bb69ce2adce000244288002b0341894729666d66adb9b4f25'),
        @('libcurand/windows-x86_64/libcurand-windows-x86_64-10.3.10.19-archive.zip', 'd0411f0b8c07e90d0fb6e01bfa7a54c9cb80f2ddf67e4ded2d96a50e19aadad6'),
        @('libcufft/windows-x86_64/libcufft-windows-x86_64-11.4.1.4-archive.zip', 'f26f80bb9abff3269c548e1559e8c2b4ba58ccb8acc6095bbc6404fc962d4b80'),
        @('cuda_cccl/windows-x86_64/cuda_cccl-windows-x86_64-12.9.27-archive.zip', '17aaa7c6b8f94a417d8f3261780b7e34b9cbdfab7513bce86768623b06aa28b5'),
        @('cuda_nvtx/windows-x86_64/cuda_nvtx-windows-x86_64-12.9.79-archive.zip', 'b9d506ce9ba056bf171b60e9dada06fb3d8bed5453a6399d0541960bf9b81659'),
        @('libnvjitlink/windows-x86_64/libnvjitlink-windows-x86_64-12.9.86-archive.zip', 'ee7175da9628d47ccc92dce6d28d57ca77633e79079a2aee90e2a645edcd1384'),
        @('cuda_profiler_api/windows-x86_64/cuda_profiler_api-windows-x86_64-12.9.79-archive.zip', 'b05519f8ce4f02167bfc859358f62eb771d89ac2af3a6952a82317f2af4bc5bd')
    )
    New-Item -ItemType Directory -Force -Path $CudaHome, "$Dl\cuda" | Out-Null
    foreach ($p in $parts) {
        $zip = Join-Path "$Dl\cuda" (Split-Path $p[0] -Leaf)
        if (-not ((Test-Path $zip) -and ((Get-FileHash $zip -Algorithm SHA256).Hash -eq $p[1]))) {
            Get-File "$redist/$($p[0])" $zip
            if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $p[1]) { Remove-Item $zip; Fail "Corrupt download: $(Split-Path $zip -Leaf) - run setup again." }
        }
        $x = "$Dl\cuda\x"
        if (Test-Path $x) { Remove-Item $x -Recurse -Force }
        & $7z x $zip "-o$x" -y | Out-Null
        $top = Get-ChildItem $x -Directory | Select-Object -First 1
        & robocopy.exe $top.FullName $CudaHome /E /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail "Could not unpack $(Split-Path $zip -Leaf)" }
        Remove-Item $x -Recurse -Force
        Write-Host "    unpacked $(Split-Path $zip -Leaf)"
    }
    if (Test-Path "$CudaHome\bin\nvcc.exe") { New-Item -ItemType File -Force "$CudaHome\.complete" | Out-Null; Remove-Item "$Dl\cuda" -Recurse -Force }
}
if (Test-Path "$CudaHome\bin\nvcc.exe") { Ok $CudaHome } else { Warn 'CUDA compiler missing - textures will be skipped.' }

# ---------------------------------------------------------------- 3D engine
Say 'Hunyuan3D-2 engine (about 3.5 GB download)'
$HyRoot = "$Root\engine"
$Hy = "$HyRoot\Hunyuan3D2_WinPortable"
if (-not (Test-Path "$Hy\python_standalone\python.exe")) {
    $base = 'https://github.com/YanWenKun/Hunyuan3D-2-WinPortable/releases/download/v4-cu129'
    Get-File "$base/Hunyuan3D2_WinPortable_cu129.7z.001" "$Dl\Hunyuan3D2_WinPortable_cu129.7z.001" 2140000000
    Get-File "$base/Hunyuan3D2_WinPortable_cu129.7z.002" "$Dl\Hunyuan3D2_WinPortable_cu129.7z.002" 1347303397
    Write-Host '    extracting ...'
    & $7z x "$Dl\Hunyuan3D2_WinPortable_cu129.7z.001" "-o$HyRoot" -y | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail 'Extracting the engine failed (bad download?). Delete the downloads folder and run setup again.' }
    if (-not (Test-Path "$Hy\python_standalone\python.exe")) {
        $f = Get-ChildItem $HyRoot -Recurse -Depth 3 -Filter python.exe | Where-Object { $_.Directory.Name -eq 'python_standalone' } | Select-Object -First 1
        if (-not $f) { Fail "Engine extracted but python_standalone was not found in $HyRoot." }
        $Hy = $f.Directory.Parent.FullName
    }
    Remove-Item "$Dl\Hunyuan3D2_WinPortable_cu129.7z.00*" -ErrorAction SilentlyContinue
}
$Py = "$Hy\python_standalone\python.exe"
Ok $Hy

$env:Path = "$env:Path;$Hy\MinGit\cmd;$Hy\python_standalone\Scripts"
$env:HF_HUB_CACHE = "$Hy\HuggingFaceHub"
$env:HY3DGEN_MODELS = "$Hy\HuggingFaceHub"
$env:PYTHONPYCACHEPREFIX = "$Hy\pycache"

Say 'Engine one-time fixes'
$marker = "$Hy\.castforge-hf-fixed"
if (-not (Test-Path $marker)) {
    & $Py -sm pip uninstall --yes huggingface-hub | Out-Null
    & $Py -sm pip install 'huggingface-hub[cli,hf-xet]==0.36.0'
    if ($LASTEXITCODE -eq 0) { New-Item -ItemType File -Force $marker | Out-Null }
}
$u2 = "$Root\tools\u2net\u2net.onnx"
if (-not (Test-Path $u2) -and (Test-Path "$Hy\extras\u2net.onnx")) {
    New-Item -ItemType Directory -Force (Split-Path $u2) | Out-Null
    Copy-Item "$Hy\extras\u2net.onnx" $u2
}
Ok 'done'

Say 'Building the texture module (one time, 5-15 minutes)'
$TexOk = $false
$Tex = "$Hy\Hunyuan3D-2\hy3dgen\texgen"
$texMarker = "$Hy\.castforge-tex-built"
if (Test-Path $texMarker) { $TexOk = $true; Ok 'already built' }
elseif ($VcVars -and (Test-Path "$CudaHome\bin\nvcc.exe")) {
    $arch = ''
    if ($Cap) { $arch = "set `"TORCH_CUDA_ARCH_LIST=$Cap`"" }
    $cmd = @"
@echo off
call "$VcVars" >nul || exit /b 90
set "DISTUTILS_USE_SDK=1"
set "CUDA_HOME=$CudaHome"
set "CUDA_PATH=$CudaHome"
set "PATH=$CudaHome\bin;%PATH%"
set "NVCC_APPEND_FLAGS=-allow-unsupported-compiler"
set "PYTHONPYCACHEPREFIX=$Hy\pycache"
$arch
"$Py" -sm pip install --no-build-isolation diso
"$Py" -sm pip install --no-build-isolation "$Tex\custom_rasterizer" || exit /b 1
"$Py" -sm pip install --no-build-isolation "$Tex\differentiable_renderer" || exit /b 2
exit /b 0
"@
    $cmdFile = "$Dl\build_texture.cmd"
    [IO.File]::WriteAllText($cmdFile, ($cmd -replace "`r?`n", "`r`n"))
    Push-Location $Tex
    & cmd.exe /c $cmdFile
    $rc = $LASTEXITCODE
    Pop-Location
    $pyd = $null
    if (Test-Path "$Tex\differentiable_renderer\build") { $pyd = Get-ChildItem "$Tex\differentiable_renderer\build" -Recurse -Filter 'mesh_processor*.pyd' | Select-Object -First 1 }
    if ($pyd) { Copy-Item $pyd.FullName "$Tex\differentiable_renderer\" -Force }
    if ($rc -eq 0) {
        $TexOk = $true; New-Item -ItemType File -Force $texMarker | Out-Null; Ok 'texture module built'
        $mf = @(); if (Test-Path -LiteralPath $Manifest) { $mf = @(Get-Content -LiteralPath $Manifest) }
        if ($mf -contains 'winget:Microsoft.VisualStudio.2022.BuildTools') {
            Say 'Removing the C++ build tools from C: (only needed for that one compile)'
            & winget uninstall -e --id Microsoft.VisualStudio.2022.BuildTools --silent --accept-source-agreements --disable-interactivity
            if (-not (Find-VC)) {
                Set-Content -LiteralPath $Manifest -Value ($mf | Where-Object { $_ -ne 'winget:Microsoft.VisualStudio.2022.BuildTools' })
                Ok 'removed - C: space freed'
            } else { Warn 'Could not remove them automatically - uninstall "Visual Studio Build Tools 2022" in Settings > Apps.' }
        }
    }
    else { Warn "Texture build failed (code $rc). CastForge will still make untextured models. Details are in the log." }
}
else { Warn 'Skipped (missing C++ tools or CUDA compiler) - untextured models only.' }

# ---------------------------------------------------------------- CastForge app
Say 'CastForge app'
$App = "$Root\app"
New-Item -ItemType Directory -Force -Path $App | Out-Null
$zip = "$Dl\castforge-app.zip"
[IO.File]::WriteAllBytes($zip, [Convert]::FromBase64String(($Payload -replace '\s', '')))
Expand-Archive -Path $zip -DestinationPath $App -Force
Remove-Item $zip

$Uv = "$Root\tools\uv.exe"
if (-not (Test-Path $Uv)) {
    Get-File 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' "$Dl\uv.zip"
    # antivirus often locks a freshly unpacked .exe for a few seconds - unpack fresh and retry
    for ($i = 0; ($i -lt 6) -and -not (Test-Path $Uv); $i++) {
        $ut = "$Root\tmp\uv-unpack-$i-$(Get-Random)"
        & $7z x "$Dl\uv.zip" "-o$ut" -y | Out-Null
        $u = Get-ChildItem $ut -Recurse -Filter uv.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($u) {
            Start-Sleep -Seconds 3
            try { Copy-Item $u.FullName $Uv -ErrorAction Stop } catch { Write-Host '    uv.exe is locked (antivirus scan?) - retrying ...'; Start-Sleep -Seconds 5 }
        }
    }
    if (-not (Test-Path $Uv)) { Fail 'Could not unpack uv.exe - your antivirus may be blocking it. Allow F:\CastForge in your antivirus, then run setup again.' }
    try { Remove-Item "$Dl\uv.zip" -Force -ErrorAction Stop } catch {}
}
$env:UV_PYTHON_INSTALL_DIR = "$Root\tools\python"
$env:UV_CACHE_DIR = "$Root\tools\uv-cache"
$VPy = "$App\.venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) {
    & $Uv venv "$App\.venv" --python 3.11
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create the Python environment.' }
}
Write-Host '    installing PyTorch (CUDA) ...'
& $Uv pip install --python $VPy torch --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { Fail 'PyTorch install failed.' }
& $Uv pip install --python $VPy -r "$App\requirements.txt"
if ($LASTEXITCODE -ne 0) { Fail 'CastForge requirements failed to install.' }
$cuda = (& $VPy -c "import torch; print(torch.cuda.is_available())")
if ("$cuda".Trim() -ne 'True') { Warn 'PyTorch cannot see your GPU - concept images will be slow (CPU).' } else { Ok 'PyTorch sees your GPU' }

$cfg = [ordered]@{ engine_url = 'http://127.0.0.1:8081'; blender = $Blender; output_dir = "$App\outputs"; engine_log = "$Root\engine.log" }
$cfg | ConvertTo-Json | Set-Content -Path "$App\config.json" -Encoding ASCII

# ---------------------------------------------------------------- launcher + shortcut
$texFlag = ''
if ($TexOk) { $texFlag = '--enable_tex' }
$start = @"
try { `$Host.UI.RawUI.WindowTitle = 'CastForge'; `$Host.UI.RawUI.BackgroundColor = 'Black'; Clear-Host } catch {}
`$HY = '$Hy'
`$env:HF_HUB_CACHE = "`$HY\HuggingFaceHub"
`$env:HY3DGEN_MODELS = "`$HY\HuggingFaceHub"
`$env:PYTHONPYCACHEPREFIX = "`$HY\pycache"
`$env:Path += ";`$HY\MinGit\cmd;`$HY\python_standalone\Scripts"
`$env:U2NET_HOME = '$Root\tools\u2net'
`$env:HF_HOME = '$Root\tools\hf-home'
`$env:TORCH_HOME = '$Root\tools\torch-home'
`$env:GRADIO_TEMP_DIR = '$Root\tmp\gradio'
`$env:TEMP = '$Root\tmp'; `$env:TMP = '$Root\tmp'
`$env:PYTHONUNBUFFERED = '1'
`$env:CASTFORGE_ENGINE_LOG = '$Root\engine.log'
`$up = `$false
try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8081/status/x -TimeoutSec 2 | Out-Null; `$up = `$true } catch {}
if (-not `$up) {
    Start-Process -FilePath "`$HY\python_standalone\python.exe" -WorkingDirectory "`$HY\Hunyuan3D-2" -WindowStyle Minimized ``
        -ArgumentList @('-s', '"$App\castforge_engine.py"', '--profile', '$MmgpProfile'$(if ($TexOk) { ", '--enable_tex'" }))
}
Write-Host 'Starting CastForge... the 3D engine loads in the minimised python window.' -ForegroundColor DarkYellow
Write-Host '(The first start downloads about 10 GB of models.)' -ForegroundColor DarkGray
Set-Location '$App'
& '$VPy' app.py
Read-Host 'CastForge stopped. Press Enter to close'
"@
$StartPs1 = "$App\Start CastForge.ps1"
[IO.File]::WriteAllText($StartPs1, ($start -replace "`r?`n", "`r`n"))
[IO.File]::WriteAllText("$Root\Uninstall CastForge.bat", ([Text.Encoding]::ASCII.GetString([Convert]::FromBase64String(($Uninstaller -replace '\s', '')))))

$PsExe = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = $ws.CreateShortcut("$desktop\CastForge.lnk")
$lnk.TargetPath = $PsExe
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartPs1`""
$lnk.WorkingDirectory = $App
$lnk.IconLocation = "$Blender,0"
$lnk.Save()
Remember "file:$desktop\CastForge.lnk"
$ul = $ws.CreateShortcut("$Root\Uninstall CastForge.lnk")
$ul.TargetPath = $PsExe
$ul.Arguments = "-NoProfile -ExecutionPolicy Bypass -Command `"`$env:CF_SELF='$Root\Uninstall CastForge.bat'; iex ([IO.File]::ReadAllText(`$env:CF_SELF))`""
$ul.WorkingDirectory = $Root
$ul.Save()
Ok 'Desktop shortcut created'
Ok "Uninstaller: $Root\Uninstall CastForge (shortcut)"

try { Stop-Transcript | Out-Null } catch {}
Write-Host ''
Write-Host '=================================================================' -ForegroundColor Green
Write-Host ' CastForge is installed.' -ForegroundColor Green
Write-Host " Textures: $(if ($TexOk) { 'ON' } else { 'OFF (see log)' })   Engine mode: profile $MmgpProfile"
Write-Host ' Start it any time with the CastForge icon on your desktop.'
Write-Host " To remove it: double-click $Root\Uninstall CastForge"
if ($NeedDriver) { Write-Host " Your NVIDIA driver ($Driver) is old - update it to 576.57 or newer before using CastForge." -ForegroundColor Yellow }
Write-Host '=================================================================' -ForegroundColor Green
$a = Read-Host 'Start CastForge now? (Y/n)'
if ($a -notmatch '^[nN]') { Start-Process explorer.exe -ArgumentList "`"$desktop\CastForge.lnk`"" }
