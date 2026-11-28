param(
    [string] $InstallRoot = (Join-Path $env:LOCALAPPDATA 'GCCP7-Native-CLI'),
    [string] $Python = '',
    [string] $MsvcRoot = $env:GCCP7_MSVC_ROOT,
    [string] $WindowsSdkRoot = $env:GCCP7_WINDOWS_SDK_ROOT,
    [string] $WindowsSdkVersion = $env:GCCP7_WINDOWS_SDK_VERSION,
    [string] $QtRoot = $env:GCCP7_QT_ROOT,
    [string] $GccpRoot = $env:GCCP7_INSTALL_ROOT,
    [switch] $DownloadQt,
    [switch] $NoSkillInstall,
    [switch] $NoPersist
)

$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$wheel = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'gccp7_native_cli-*.whl')
if ($wheel.Count -ne 1) { throw 'Extract the Windows Build zip. Run its setup-windows.ps1 next to the CLI wheel.' }
if ($Python) { $pythonArgs = @() } else { $Python = 'py'; $pythonArgs = @('-3.12') }
& $Python @pythonArgs -c 'import struct,sys;assert sys.version_info[:2]==(3,12) and struct.calcsize("P")==8'
if ($LASTEXITCODE -ne 0) { throw 'Install Windows x64 Python 3.12 (including the Python launcher), or pass its python.exe with -Python.' }

if (!$MsvcRoot) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($vs) {
            $candidate = Get-ChildItem (Join-Path $vs 'VC/Tools/MSVC') -Directory |
                Where-Object { Test-Path (Join-Path $_.FullName 'bin/Hostx64/x64/cl.exe') } |
                Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
            if ($candidate) { $MsvcRoot = $candidate.FullName }
        }
    }
}
if (!$WindowsSdkRoot) { $WindowsSdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10' }
if (!$WindowsSdkVersion -and (Test-Path (Join-Path $WindowsSdkRoot 'Include'))) {
    $candidate = Get-ChildItem (Join-Path $WindowsSdkRoot 'Include') -Directory |
        Where-Object { $_.Name -match '^10\.0\.\d+\.\d+$' -and (Test-Path (Join-Path $WindowsSdkRoot "Lib/$($_.Name)/ucrt/x64")) } |
        Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
    if ($candidate) { $WindowsSdkVersion = $candidate.Name }
}
if (!$QtRoot) { $QtRoot = Join-Path $InstallRoot 'qt/5.9.9/msvc2015_64' }
if (!$MsvcRoot -or !$WindowsSdkVersion) { throw 'Install Visual Studio C++ Build Tools x64 and a Windows SDK, then rerun setup. See docs/first-install.md.' }

$venv = Join-Path $InstallRoot 'venv'
$venvPython = Join-Path $venv 'Scripts/python.exe'
if (!(Test-Path -LiteralPath $venvPython)) {
    & $Python @pythonArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the CLI Python environment.' }
}
if (!(Test-Path (Join-Path $QtRoot 'lib/Qt5Core.lib')) -and $DownloadQt) {
    & $venvPython -m pip install 'aqtinstall>=3,<4'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot install aqtinstall.' }
    & $venvPython -m aqt install-qt windows desktop 5.9.9 win64_msvc2015_64 --archives qtbase -O (Join-Path $InstallRoot 'qt')
    if ($LASTEXITCODE -ne 0) { throw 'Qt archive download failed. Install Qt 5.9.9 MSVC 2015 x64 and pass -QtRoot.' }
}
$toolPaths = @{
    GCCP7_MSVC_ROOT = $MsvcRoot
    GCCP7_WINDOWS_SDK_ROOT = $WindowsSdkRoot
    GCCP7_WINDOWS_SDK_VERSION = $WindowsSdkVersion
    GCCP7_QT_ROOT = $QtRoot
}
if ($GccpRoot) { $toolPaths.GCCP7_INSTALL_ROOT = $GccpRoot }
foreach ($key in $toolPaths.Keys) { [Environment]::SetEnvironmentVariable($key, $toolPaths[$key], 'Process') }
& $venvPython -m pip install "$($wheel[0].FullName)[assistant]"
if ($LASTEXITCODE -ne 0) { throw 'CLI wheel installation failed.' }
$gccp = Join-Path $venv 'Scripts/gccp.exe'
$doctor = & $gccp doctor
if ($LASTEXITCODE -ne 0) { throw ($doctor -join "`n") }
$doctor
if (!$NoPersist) {
    foreach ($key in $toolPaths.Keys) { [Environment]::SetEnvironmentVariable($key, $toolPaths[$key], 'User') }
}
if (!$NoSkillInstall) {
    $skills = Join-Path $env:USERPROFILE '.codex/skills'
    foreach ($name in @('gccp7-native-cli','gccp7-quota-reuse')) {
        $source = Join-Path $PSScriptRoot "skills/$name"
        $target = Join-Path $skills $name
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        Copy-Item -Path (Join-Path $source '*') -Destination $target -Recurse -Force
    }
    $guide = Join-Path $skills 'gccp7-native-cli/references/local-installation.md'
    ('PowerShell: $gccp = ''{0}''. Run & $gccp doctor after opening a new terminal.' -f $gccp.Replace("'","''")) |
        Set-Content -LiteralPath $guide -Encoding utf8
}
Write-Output "CLI installed: $gccp"
& $gccp --version
