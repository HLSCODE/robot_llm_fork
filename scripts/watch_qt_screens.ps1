# Opt-in native tracing. No registry changes, downloads, SDK hooks or automatic attach.
[CmdletBinding(DefaultParameterSetName = 'Attach')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Attach')]
    [ValidateRange(1, 2147483647)][int]$TargetProcessId,
    [Parameter(Mandatory = $true, ParameterSetName = 'Launch')]
    [ValidateSet('Simulation', 'Hardware', 'Probe')][string]$Launch,
    [string]$CdbPath,
    [Parameter(ParameterSetName = 'Launch')]
    [string]$PythonPath,
    [Parameter(ParameterSetName = 'Launch')][string]$ConfigPath,
    [string]$OutputDirectory,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $PythonPath) { $PythonPath = Join-Path $PSScriptRoot '../.venv/Scripts/python.exe' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot '../logs/crash/native' }

function Resolve-X64Executable([string]$Path) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "Executable must be a file: $resolved"
    }
    $reader = [IO.BinaryReader]::new([IO.File]::OpenRead($resolved))
    try {
        if ($reader.ReadUInt16() -ne 0x5a4d) { throw 'Expected a Windows PE executable.' }
        $reader.BaseStream.Position = 0x3c
        $peOffset = $reader.ReadUInt32()
        $reader.BaseStream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x4550 -or $reader.ReadUInt16() -ne 0x8664) {
            throw 'This trace requires Windows x64 executables (AMD64), not x86 or ARM64.'
        }
    } finally {
        $reader.Dispose()
    }
    return $resolved
}

function Find-Cdb {
    $command = Get-Command cdb.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/Debuggers/x64/cdb.exe'
    if (Test-Path -LiteralPath $kits -PathType Leaf) { return $kits }
    if ($null -ne (Get-Command Get-AppxPackage -ErrorAction SilentlyContinue)) {
        $package = Get-AppxPackage -Name Microsoft.WinDbg | Select-Object -First 1
        if ($null -ne $package) {
            $candidate = Join-Path $package.InstallLocation 'amd64/cdb.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    throw 'CDB not found. Install WinDbg / Debugging Tools for Windows, or specify -CdbPath.'
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Native screen tracing currently supports Windows x64 only.'
}
if (-not $CdbPath) { $CdbPath = Find-Cdb }
$debugger = Resolve-X64Executable $CdbPath
if ([IO.Path]::GetFileName($debugger) -ine 'cdb.exe') {
    throw 'CdbPath must name cdb.exe, not WinDbg.exe or another executable.'
}
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$mode = $PSCmdlet.ParameterSetName
$targetArgs = @()
if ($mode -eq 'Attach') {
    $target = Get-Process -Id $TargetProcessId
    if ($target.ProcessName -notin @('python', 'pythonw', 'robot-llm')) {
        throw "Refusing unexpected process: $($target.ProcessName)"
    }
    $executable = Resolve-X64Executable $target.Path
    $targetArgs = @('-p', "$TargetProcessId")
    $description = "Attach to PID=$TargetProcessId ($executable)"
} else {
    $executable = Resolve-X64Executable $PythonPath
    # Windows venv python.exe may forward to a child interpreter: follow it too.
    $targetArgs = @('-o', $executable)
    if ($Launch -eq 'Probe') {
        if ($ConfigPath) { throw '-ConfigPath is not applicable to the hardware-free Probe.' }
        $targetArgs += (Join-Path $PSScriptRoot 'probes/qt_screen_lifecycle.py')
    } else {
        $targetArgs += @('-m', 'src.bootstrap.launcher')
        if ($Launch -eq 'Simulation') { $targetArgs += '--simulation' }
        if ($ConfigPath) {
            $targetArgs += @('--config', (Resolve-Path -LiteralPath $ConfigPath).Path)
        }
    }
    $description = "Launch $Launch with $executable (child processes included)"
}

$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
# CDB command files use legacy encodings and debugger aliases. Fail clearly rather
# than emitting a corrupted path or allowing a path to inject debugger commands.
if ($outputRoot -match '[^\x20-\x7e]|[";$`]') {
    throw 'OutputDirectory must be an ASCII path without quotes, semicolons, $ or backticks. Spaces are supported.'
}
$sessionName = 'qt-screens-{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N'))
$sessionDirectory = Join-Path $outputRoot $sessionName
$commandPath = Join-Path $sessionDirectory 'trace.cdb'
$vtableCommandPath = Join-Path $sessionDirectory 'screen-vtable.cdb'
$logPath = Join-Path $sessionDirectory 'native-screen.log'
$dumpPath = (Join-Path $sessionDirectory 'unhandled.dmp').Replace('\', '/')
$templatePath = Join-Path $PSScriptRoot 'debugger/qt_screen_lifecycle.cdb'
$commands = [IO.File]::ReadAllText($templatePath).Replace('@@DUMP_PATH@@', $dumpPath)
$commands = $commands.Replace('@@COMMAND_PATH@@', $commandPath.Replace('\', '/'))
$commands = $commands.Replace('@@VTABLE_COMMAND_PATH@@', $vtableCommandPath.Replace('\', '/'))
# Both -cf and nested $$< must see Windows line endings (even with core.autocrlf=false).
$commands = $commands.Replace("`r`n", "`n").Replace("`n", "`r`n")
# -pd detaches on debugger exit; -hd avoids changing the target to the debug heap.
# Do not use -g: the startup breakpoint must install tracing before Qt is imported.
$debuggerArgs = @('-pd', '-hd', '-G', '-y', (Join-Path $outputRoot 'symbols'),
    '-logo', $logPath, '-cf', $commandPath) + $targetArgs

if ($DryRun) {
    [ordered]@{
        mode = $mode
        description = $description
        debugger = $debugger
        arguments = $debuggerArgs
        working_directory = $workspace
        session_directory = $sessionDirectory
        commands = $commands
    } | ConvertTo-Json -Depth 3
    return
}

Write-Host $description
Write-Host "Trace log: $logPath"
Write-Host 'STOP all robot motion first. Debugger breakpoints and dumps can pause the target.'
Write-Host 'Stop any ProcDump/WinDbg monitor attached to this PID before continuing.'
Write-Host 'Native stacks go to the log only. Full dumps may contain credentials and camera images.'
Write-Host 'To stop tracing: press Ctrl+C, wait for the CDB prompt, then enter qd. Do not close the terminal.'
if ($Launch -eq 'Hardware') {
    Write-Warning 'Hardware launch uses your real configuration and may initialize/enable devices.'
}
if ((Read-Host 'Start native screen tracing? Type YES to continue') -cne 'YES') { return }

# Check identity again after the confirmation: do not attach to a recycled PID.
if ($mode -eq 'Attach') {
    $currentTarget = Get-Process -Id $TargetProcessId
    if ($currentTarget.StartTime -ne $target.StartTime -or $currentTarget.Path -ne $executable) {
        throw 'Target process identity changed while awaiting confirmation. Run the command again.'
    }
}
[IO.Directory]::CreateDirectory($sessionDirectory) | Out-Null
[IO.Directory]::CreateDirectory((Join-Path $outputRoot 'symbols')) | Out-Null
[IO.File]::WriteAllText($commandPath, $commands, [Text.Encoding]::ASCII)
$vtableCommands = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'debugger/qt_screen_vtable.cdb'))
$vtableCommands = $vtableCommands.Replace("`r`n", "`n").Replace("`n", "`r`n")
[IO.File]::WriteAllText($vtableCommandPath, $vtableCommands, [Text.Encoding]::ASCII)
$metadata = [ordered]@{
    started_at = (Get-Date).ToUniversalTime().ToString('o')
    mode = $mode
    description = $description
    debugger = $debugger
    arguments = $debuggerArgs
    working_directory = $workspace
}
$metadataPath = Join-Path $sessionDirectory 'session.json'
[IO.File]::WriteAllText($metadataPath, ($metadata | ConvertTo-Json -Depth 3), [Text.Encoding]::UTF8)
Push-Location -LiteralPath $workspace
try {
    & $debugger @debuggerArgs
    $debuggerExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
$metadata['debugger_exit_code'] = $debuggerExitCode
$metadata['finished_at'] = (Get-Date).ToUniversalTime().ToString('o')
$traceText = [IO.File]::ReadAllText($logPath)
$created = [regex]::Matches($traceText, '(?m)^QT_SCREEN_CTOR pid=(\d+) tid=\d+ screen=([0-9a-fA-F]+)')
$destroyed = [regex]::Matches($traceText, '(?m)^QT_SCREEN_(?:VIRTUAL_DTOR|DTOR) pid=(\d+) tid=\d+ screen=([0-9a-fA-F]+)')
$vtableArmed = [regex]::IsMatch($traceText, '(?m)^QT_SCREEN_VIRTUAL_DTOR_ARMED\r?$')
$metadata['screen_created_events'] = $created.Count
$metadata['screen_destroyed_events'] = $destroyed.Count
$metadata['virtual_destructor_armed'] = $vtableArmed
[IO.File]::WriteAllText($metadataPath, ($metadata | ConvertTo-Json -Depth 3), [Text.Encoding]::UTF8)
Write-Host "Tracing ended (CDB exit code $debuggerExitCode). Log: $logPath"
Write-Host "Screen events: created=$($created.Count), destroyed=$($destroyed.Count), virtual destructor armed=$vtableArmed"
if ($Launch -eq 'Probe') {
    $createdIdentities = @($created | ForEach-Object { $_.Groups[1].Value + ':' + $_.Groups[2].Value })
    $matchedDestruction = @($destroyed | Where-Object {
        ($_.Groups[1].Value + ':' + $_.Groups[2].Value) -in $createdIdentities
    })
    if ($debuggerExitCode -ne 0 -or -not $vtableArmed -or $matchedDestruction.Count -eq 0) {
        throw 'Probe failed: no verified create/destroy pair. Inspect native-screen.log; configured/pending breakpoints alone do not prove tracing works.'
    }
    Write-Host 'Probe verified: matching screen PID/address observed at creation and destruction.'
} elseif (-not $vtableArmed) {
    Write-Warning 'No virtual-destructor installation was confirmed. Qt may not have loaded or its ABI/symbols differ. Run -Launch Probe to validate this environment.'
}
if ($debuggerExitCode -ne 0) {
    Write-Warning 'Check native-screen.log for target failure vs debugger startup failure; a nonzero code alone does not mean dump capture failed.'
}
