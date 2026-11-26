# Remove an installation made by install.ps1 (files and the user PATH entry).
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-Prefix DIR]
param([string]$Prefix = (Join-Path $env:LOCALAPPDATA 'Programs\jfixpass'))
$ErrorActionPreference = 'Stop'
$bin = Join-Path $Prefix 'bin'
if (Test-Path (Join-Path $Prefix 'lib\jfixpass.jar')) {
    Remove-Item -Recurse -Force $Prefix
}
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) {
    $kept = ($userPath -split ';') | Where-Object { $_ -and $_ -ne $bin }
    [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
}
Write-Host "removed JFixPass from $Prefix"
