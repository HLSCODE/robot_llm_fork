param(
    [string]$ModelDir = "models\kws\sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20",
    [string]$InputPath = "models\kws\keywords_raw.txt",
    [string]$OutputPath = "models\kws\keywords.txt",
    [ValidateSet("phone+ppinyin", "ppinyin", "fpinyin", "cjkchar")]
    [string]$TokensType = "phone+ppinyin"
)

$ErrorActionPreference = "Stop"

$tokens = Join-Path $ModelDir "tokens.txt"
$lexicon = Join-Path $ModelDir "en.phone"

if (-not (Test-Path $tokens)) {
    throw "tokens.txt not found: $tokens"
}
if (-not (Test-Path $InputPath)) {
    throw "raw keywords file not found: $InputPath"
}

$args = @(
    "run",
    "sherpa-onnx-cli",
    "text2token",
    "--tokens",
    $tokens,
    "--tokens-type",
    $TokensType
)

if ($TokensType -eq "phone+ppinyin") {
    if (-not (Test-Path $lexicon)) {
        throw "lexicon not found: $lexicon"
    }
    $args += @("--lexicon", $lexicon)
}

$args += @($InputPath, $OutputPath)

uv @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
Write-Host "Generated KWS keywords: $OutputPath"
