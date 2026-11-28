param([string] $Python = 'python', [string] $Output, [string] $WheelPython)

$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    $version = (& $Python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw 'Cannot read build version' }
    if (-not $Output) { $Output = Join-Path $PWD "dist/build-$version" }
    $Output = [IO.Path]::GetFullPath($Output)
    if (Test-Path -LiteralPath $Output) { throw 'Choose a new build directory' }
    $bundle = Join-Path $Output 'package'
    New-Item -ItemType Directory -Path $bundle | Out-Null
    if (-not $WheelPython) { $WheelPython = $Python }
    & $WheelPython -m pip wheel --no-deps --no-build-isolation --wheel-dir $bundle .
    if ($LASTEXITCODE -ne 0) { throw 'CLI wheel build failed' }
    & $Python -m PyInstaller --noconfirm --onedir --windowed --name GCCP7-Price-Assistant --paths src --distpath $bundle --workpath (Join-Path $Output 'pyinstaller') --specpath $Output src/gccp_material_price_assistant/material_prices.py
    if ($LASTEXITCODE -ne 0) { throw 'Assistant build failed' }
    Rename-Item -LiteralPath (Join-Path $bundle 'GCCP7-Price-Assistant') -NewName 'price-assistant'
    Copy-Item -LiteralPath README.md, LICENSE, THIRD-PARTY-NOTICES.md -Destination $bundle
    Copy-Item -LiteralPath examples/setup-windows.ps1 -Destination $bundle
    Copy-Item -LiteralPath docs -Destination $bundle -Recurse
    Copy-Item -LiteralPath .agents/skills -Destination $bundle -Recurse
    foreach ($skill in Get-ChildItem -LiteralPath (Join-Path $bundle 'skills') -Directory) {
        $references = Join-Path $skill.FullName 'references'
        New-Item -ItemType Directory -Path $references -Force | Out-Null
        Copy-Item -LiteralPath README.md -Destination $references
        Copy-Item -LiteralPath docs -Destination $references -Recurse
    }
    $zip = Join-Path $Output "Gccp7NativeCli-v$version-Windows-Build.zip"
    Compress-Archive -Path (Join-Path $bundle '*') -DestinationPath $zip
    $hash = (Get-FileHash -LiteralPath $zip).Hash.ToLower()
    "$hash  $([IO.Path]::GetFileName($zip))" | Set-Content (Join-Path $Output 'SHA256SUMS.txt')
    Get-Item -LiteralPath $zip
} finally {
    Pop-Location
}
