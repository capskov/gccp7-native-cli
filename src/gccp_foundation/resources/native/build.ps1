$ErrorActionPreference = 'Stop'
$vc = $env:GCCP7_MSVC_ROOT
$sdk = $env:GCCP7_WINDOWS_SDK_ROOT
$version = $env:GCCP7_WINDOWS_SDK_VERSION
$qt = $env:GCCP7_QT_ROOT
foreach ($root in @($vc, $sdk, $qt)) {
    if (!$root -or ![IO.Path]::IsPathRooted($root) -or !(Test-Path -LiteralPath $root -PathType Container)) {
        throw 'Set absolute GCCP7_MSVC_ROOT, GCCP7_WINDOWS_SDK_ROOT and GCCP7_QT_ROOT directories.'
    }
}
if ($version -notmatch '^\d+\.\d+\.\d+\.\d+$') { throw 'Set GCCP7_WINDOWS_SDK_VERSION, e.g. 10.0.26100.0.' }
$compiler = Join-Path $vc 'bin/Hostx64/x64/cl.exe'
$dumpbin = Join-Path $vc 'bin/Hostx64/x64/dumpbin.exe'
$output = Join-Path $PSScriptRoot 'build'
$env:INCLUDE = "$vc/include;$sdk/Include/$version/ucrt;$sdk/Include/$version/shared;$sdk/Include/$version/um"
$env:LIB = "$vc/lib/x64;$sdk/Lib/$version/um/x64;$sdk/Lib/$version/ucrt/x64"
$env:PATH = "$vc/bin/Hostx64/x64;$qt/bin;$env:PATH"
New-Item -ItemType Directory -Path $output | Out-Null
$source = Join-Path $PSScriptRoot 'bridge.cpp'
$binary = Join-Path $output 'gccp_business.dll'
& $compiler /nologo /LD /EHsc /MD /utf-8 /std:c++14 /DQT_NO_DEBUG /DNDEBUG "/I$qt/include" "/I$qt/include/QtCore" "/I$qt/include/QtGui" "/I$qt/include/QtWidgets" "/Fo$output/bridge.obj" "/Fe$binary" $source /link "/LIBPATH:$qt/lib" Qt5Core.lib Qt5Gui.lib Qt5Widgets.lib
if ($LASTEXITCODE -ne 0) { throw 'Native bridge compilation failed.' }
[ordered]@{source=$source;source_sha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash;binary_sha256=(Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash;dumpbin=$dumpbin} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'build-provenance.json') -Encoding utf8
Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'operations.h') -Algorithm SHA256 | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'operations-provenance.json') -Encoding utf8
