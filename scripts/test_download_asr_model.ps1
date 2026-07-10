param(
    [string]$AsrModel = "",
    [string]$VadModel = "",
    [string]$PuncModel = "",
    [string]$Device = "",
    [switch]$ShowModelOutput,
    [switch]$SkipVad,
    [switch]$SkipAsr
)

$ErrorActionPreference = "Stop"

$argsList = @("scripts/test_download_asr_model.py")
if ($AsrModel) {
    $argsList += @("--asr-model", $AsrModel)
}
if ($VadModel) {
    $argsList += @("--vad-model", $VadModel)
}
if ($PSBoundParameters.ContainsKey("PuncModel")) {
    $argsList += @("--punc-model", $PuncModel)
}
if ($Device) {
    $argsList += @("--device", $Device)
}
if ($ShowModelOutput) {
    $argsList += "--show-model-output"
}
if ($SkipVad) {
    $argsList += "--skip-vad"
}
if ($SkipAsr) {
    $argsList += "--skip-asr"
}

uv run python @argsList
