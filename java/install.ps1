# Install JFixPass as a command-line compiler (Windows).
#
#   powershell -ExecutionPolicy Bypass -File install.ps1            # installs to %LOCALAPPDATA%\Programs\jfixpass
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Prefix D:\tools\jfixpass
#
# Requires a JDK 17+ (javac must be on PATH: JFixPass drives the JDK's own compiler).
# Adds <Prefix>\bin to the user PATH; open a new terminal afterwards.
param([string]$Prefix = (Join-Path $env:LOCALAPPDATA 'Programs\jfixpass'))
$ErrorActionPreference = 'Stop'
$Version = '0.1.0'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

foreach ($tool in 'java', 'javac', 'jar') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Error "install.ps1: a JDK 17 or newer is required ($tool not found on PATH)."
    }
}
$jv = (& javac -version 2>&1 | Out-String)
if ($jv -match 'javac (\d+)') {
    if ([int]$Matches[1] -lt 17) { Write-Error "install.ps1: JDK 17 or newer required (found: $($jv.Trim()))." }
} else {
    Write-Error "install.ps1: could not read the javac version: $jv"
}

Write-Host "building JFixPass $Version ..."
$build = Join-Path $Here 'build'
if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force -Path (Join-Path $build 'classes') | Out-Null
$sources = Get-ChildItem -Path (Join-Path $Here 'src\jfixpass') -Filter '*.java' | ForEach-Object { $_.FullName }
& javac -Xlint:all -Werror -d (Join-Path $build 'classes') @sources
if ($LASTEXITCODE -ne 0) { Write-Error 'install.ps1: javac failed' }
& jar --create --file (Join-Path $build 'jfixpass.jar') --main-class jfixpass.Main -C (Join-Path $build 'classes') .
if ($LASTEXITCODE -ne 0) { Write-Error 'install.ps1: jar failed' }

$lib = Join-Path $Prefix 'lib'
$bin = Join-Path $Prefix 'bin'
New-Item -ItemType Directory -Force -Path $lib, $bin | Out-Null
Copy-Item -Force (Join-Path $build 'jfixpass.jar') (Join-Path $lib 'jfixpass.jar')

$launcher = @'
# JFixPass __VERSION__ launcher (installed by install.ps1)
#   jfixpass Source.java [--tests T.json] [--extra-tests E.json] [--spec S.txt] [--out CLASSDIR] [--work-dir DIR]
# Without --work-dir, results go to .\jfixpass-out\<Source>\ (replaced on each run).
$jar = Join-Path (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)) 'lib\jfixpass.jar'
$argv = @($args)
if ($argv.Count -gt 0 -and $argv[0] -eq '--version') { Write-Output 'jfixpass __VERSION__'; exit 0 }
$hasWd = $false; $src = $null; $wd = $null
for ($i = 0; $i -lt $argv.Count; $i++) {
    switch -Exact ($argv[$i]) {
        '--work-dir' { $hasWd = $true; if ($i + 1 -lt $argv.Count) { $wd = $argv[$i + 1] }; $i++ }
        { $_ -in '--tests', '--extra-tests', '--spec', '--out', '-o' } { $i++ }
        default { if (-not $argv[$i].StartsWith('-')) { $src = $argv[$i] } }
    }
}
if (-not $hasWd -and $src) {
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($src)
    $wd = Join-Path 'jfixpass-out' $stem
    if (Test-Path $wd) {
        $empty = -not (Get-ChildItem -Force $wd | Select-Object -First 1)
        if ((Test-Path (Join-Path $wd 'report.json')) -or $empty) {
            Remove-Item -Recurse -Force $wd
        } else {
            [Console]::Error.WriteLine("jfixpass: $wd exists and is not a jfixpass work directory")
            exit 2
        }
    }
    New-Item -ItemType Directory -Force -Path 'jfixpass-out' | Out-Null
    $argv += @('--work-dir', $wd)
}
& java -Xss64m -XX:+UseSerialGC -jar $jar @argv
$code = $LASTEXITCODE
if ($wd -and (Test-Path (Join-Path $wd 'report.json'))) {
    if (Test-Path (Join-Path $wd 'corrected.java')) {
        Write-Output "corrected source: $wd\corrected.java   classes: $wd\classes   report: $wd\report.json"
    } else {
        Write-Output "candidate source: $wd\candidate.java   report: $wd\report.json"
    }
}
exit $code
'@
Set-Content -Encoding UTF8 -Path (Join-Path $bin 'jfixpass.ps1') -Value ($launcher -replace '__VERSION__', $Version)
$cmd = "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0jfixpass.ps1`" %*`r`nexit /b %ERRORLEVEL%`r`n"
[System.IO.File]::WriteAllText((Join-Path $bin 'jfixpass.cmd'), $cmd)

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not $userPath) { $userPath = '' }
if (($userPath -split ';') -notcontains $bin) {
    $newPath = if ($userPath) { "$userPath;$bin" } else { $bin }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Host "added $bin to the user PATH (open a new terminal)"
}
Write-Host "installed: $bin\jfixpass.cmd"
Write-Host "           $lib\jfixpass.jar"
