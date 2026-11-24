#Requires -Version 5.1
<#
  一键构建：PyInstaller 打 exe（onedir），WiX 打 MSI，并生成便携版 zip。
  产物：
    dist\GXUSTPower\GXUSTPower.exe            主程序（绿软目录）
    dist\GXUSTPower-1.0.1-portable.zip       便携版压缩包
    dist\GXUSTPower-1.0.1.msi                安装包（装到 %LOCALAPPDATA%\Programs\DormPower）
#>
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$Version = '1.0.1'
$WixDir = Join-Path $PSScriptRoot 'build\tools\wix'
$Candle = Join-Path $WixDir 'candle.exe'
$Light = Join-Path $WixDir 'light.exe'

foreach ($tool in @($Candle, $Light)) {
    if (-not (Test-Path $tool)) {
        throw "缺少 WiX 工具：$tool`n请把 wix314-binaries.zip 解压到 build\tools\wix"
    }
}

# WiX 需要可写的临时目录
$TempDir = Join-Path $PSScriptRoot 'build\tmp'
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
$env:TMP = $TempDir
$env:TEMP = $TempDir

Remove-Item -Recurse -Force (Join-Path $PSScriptRoot 'dist\GXUSTPower') -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $PSScriptRoot 'dist\GXUSTPower.exe') -ErrorAction SilentlyContinue

Write-Host '==> [1/5] PyInstaller 生成程序目录' -ForegroundColor Cyan
$py = (Get-Command python).Source
& $py -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name GXUSTPower `
    --icon (Join-Path $PSScriptRoot 'assets\app.ico') `
    --version-file (Join-Path $PSScriptRoot 'tools\version_info.txt') `
    --distpath (Join-Path $PSScriptRoot 'dist') `
    --workpath (Join-Path $PSScriptRoot 'build\pyi') `
    --specpath (Join-Path $PSScriptRoot 'build') `
    --add-data "$PSScriptRoot\assets;assets" `
    --version-file "$PSScriptRoot\tools\version_info.txt" `
    --exclude-module numpy `
    app.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败' }

$exe = Join-Path $PSScriptRoot 'dist\GXUSTPower\GXUSTPower.exe'
if (-not (Test-Path $exe)) { throw '没有生成 dist\GXUSTPower\GXUSTPower.exe' }
$size = (Get-ChildItem (Join-Path $PSScriptRoot 'dist\GXUSTPower') -Recurse -File |
         Measure-Object Length -Sum).Sum
Write-Host '==> [1b/5] 精简未使用的图片解码库' -ForegroundColor Cyan
$internal = Join-Path $PSScriptRoot 'dist\GXUSTPower\_internal'
$pilDir = Join-Path $internal 'PIL'
foreach ($pattern in @('_avif*.pyd', '_webp*.pyd', '_imagingcms*.pyd', '_imagingft*.pyd')) {
    Get-ChildItem -Path $pilDir -Filter $pattern -ErrorAction SilentlyContinue | Remove-Item -Force
}
Get-ChildItem -Path $internal -Directory -Filter 'numpy*' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$uninstallExe = Join-Path $PSScriptRoot 'dist\GXUSTPower\uninstall.exe'
if (Test-Path $csc) {
    & $csc /nologo /target:winexe /win32icon:"$PSScriptRoot\assets\app.ico" `
        /win32manifest:"$PSScriptRoot\tools\setup.manifest" `
        /r:System.Windows.Forms.dll "/out:$uninstallExe" `
        (Join-Path $PSScriptRoot 'tools\uninstall_launcher.cs')
    if ($LASTEXITCODE -ne 0) { throw '卸载程序编译失败' }
}

$size = (Get-ChildItem (Join-Path $PSScriptRoot 'dist\GXUSTPower') -Recurse -File |
         Measure-Object Length -Sum).Sum
Write-Host ("    程序目录: {0:N1} MB" -f ($size / 1MB)) -ForegroundColor Green

Write-Host '==> [2/5] 生成 MSI 文件清单' -ForegroundColor Cyan
& $py (Join-Path $PSScriptRoot 'tools\gen_components.py')
if ($LASTEXITCODE -ne 0) { throw '生成文件清单失败' }

Write-Host '==> [3/5] WiX candle 编译' -ForegroundColor Cyan
$WixObjDir = Join-Path $PSScriptRoot 'build\wixobj'
New-Item -ItemType Directory -Force -Path $WixObjDir | Out-Null
& $Candle -nologo -arch x64 -ext WixUIExtension -ext WixUtilExtension "-dSourceDir=$PSScriptRoot\dist\GXUSTPower" `
    -out "$WixObjDir\" `
    (Join-Path $PSScriptRoot 'installer.wxs') (Join-Path $PSScriptRoot 'build\files.wxs')
if ($LASTEXITCODE -ne 0) { throw 'candle 编译失败' }

Write-Host '==> [4/5] WiX light 链接 MSI' -ForegroundColor Cyan
$msi = Join-Path $PSScriptRoot "dist\GXUSTPower-$Version.msi"
& $Light -nologo -sval -ext WixUIExtension -ext WixUtilExtension -cultures:zh-CN -out $msi `
    (Join-Path $WixObjDir 'installer.wixobj') (Join-Path $WixObjDir 'files.wixobj')
if ($LASTEXITCODE -ne 0) { throw 'light 链接失败' }
if (-not (Test-Path $msi)) { throw '没有生成 MSI' }
Write-Host ("    msi: {0:N1} MB" -f ((Get-Item $msi).Length / 1MB)) -ForegroundColor Green

Write-Host '==> [5/6] 生成 Setup.exe' -ForegroundColor Cyan
$setupExe = Join-Path $PSScriptRoot "dist\GXUSTPower-$Version-Setup.exe"
if (Test-Path $csc) {
    & $csc /nologo /target:winexe /win32icon:"$PSScriptRoot\assets\app.ico" `
        /win32manifest:"$PSScriptRoot\tools\setup.manifest" `
        /r:System.Windows.Forms.dll "/resource:$msi,GXUSTPower.msi" `
        "/out:$setupExe" (Join-Path $PSScriptRoot 'tools\setup_launcher.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Setup.exe 编译失败' }
    Write-Host ("    setup: {0:N1} MB" -f ((Get-Item $setupExe).Length / 1MB)) -ForegroundColor Green
}

Write-Host '==> [6/6] 单文件便携版 exe' -ForegroundColor Cyan
$portable = Join-Path $PSScriptRoot "dist\GXUSTPower-$Version-portable.exe"
$portableZip = Join-Path $PSScriptRoot 'build\portable.zip'
Remove-Item -Force $portableZip -ErrorAction SilentlyContinue
$appDir = Join-Path $PSScriptRoot 'dist\GXUSTPower'
Remove-Item -Recurse -Force (Join-Path $appDir 'data') -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $appDir 'config.json') -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $appDir '*') -DestinationPath $portableZip -CompressionLevel Optimal
& $csc /nologo /target:winexe /win32icon:"$PSScriptRoot\assets\app.ico" `
    /win32manifest:"$PSScriptRoot\tools\setup.manifest" `
    /r:System.Windows.Forms.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll `
    "/resource:$portableZip,GXUSTPower.zip" `
    "/out:$portable" (Join-Path $PSScriptRoot 'tools\portable_launcher.cs')
if ($LASTEXITCODE -ne 0) { throw '单文件便携版编译失败' }
Write-Host ("    portable: {0:N1} MB" -f ((Get-Item $portable).Length / 1MB)) -ForegroundColor Green

Write-Host ''
Write-Host '构建完成：' -ForegroundColor Green
Write-Host "  主程序    $exe"
Write-Host "  安装包    $msi"
if ($setupExe) { Write-Host "  安装程序  $setupExe" }
Write-Host "  便携版    $portable"
