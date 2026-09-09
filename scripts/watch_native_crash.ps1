# Attach only to an explicitly selected process. No registry or SDK changes.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 2147483647)][int]$TargetProcessId,
    [Parameter(Mandatory = $true)][string]$ProcDumpPath,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '../logs/crash/native')
)
$ErrorActionPreference = 'Stop'
$toolPath = (Resolve-Path -LiteralPath $ProcDumpPath).Path
if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) {
    throw 'ProcDumpPath must name the downloaded ProcDump executable.'
}
$targetProcess = Get-Process -Id $TargetProcessId
if ($targetProcess.ProcessName -notin @('python', 'pythonw', 'robot-llm')) {
    throw "Refusing unexpected process: $($targetProcess.ProcessName)"
}
$dumpDirectory = [IO.Path]::GetFullPath($OutputDirectory)
Write-Host "Target: $($targetProcess.ProcessName) PID=$TargetProcessId"
Write-Host "Executable: $($targetProcess.Path)"
Write-Host "Full dumps can contain credentials and private data; keep them local."
if ((Read-Host 'Attach crash monitor? Type YES to continue') -cne 'YES') {
    return
}
New-Item -ItemType Directory -Path $dumpDirectory -Force | Out-Null
# -e captures unhandled exceptions, not every first-chance exception.
# Do not install as a system debugger (-i) or automatically accept the EULA.
& $toolPath -ma -e -n 1 $TargetProcessId $dumpDirectory
if ($LASTEXITCODE -ne 0) {
    throw "ProcDump exited with code $LASTEXITCODE"
}
