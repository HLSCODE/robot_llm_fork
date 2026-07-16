param(
    [string]$DestDir = "models\kws",
    [ValidateSet("zh-en", "zh")]
    [string]$Model = "zh-en"
)

$ErrorActionPreference = "Stop"

$models = @{
    "zh-en" = @{
        Name = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
        Url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2"
        Encoder = "encoder-epoch-13-avg-2-chunk-16-left-64.onnx"
        Decoder = "decoder-epoch-13-avg-2-chunk-16-left-64.onnx"
        Joiner = "joiner-epoch-13-avg-2-chunk-16-left-64.onnx"
    }
    "zh" = @{
        Name = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
        Url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2"
        Encoder = "encoder-epoch-12-avg-2-chunk-16-left-64.onnx"
        Decoder = "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
        Joiner = "joiner-epoch-12-avg-2-chunk-16-left-64.onnx"
    }
}

$item = $models[$Model]
$modelName = $item.Name
$url = $item.Url
$destPath = Join-Path $DestDir $modelName
$archivePath = Join-Path $DestDir "$modelName.tar.bz2"

New-Item -ItemType Directory -Force $DestDir | Out-Null

if (Test-Path $destPath) {
    Write-Host "Model already exists: $destPath"
} else {
    Write-Host "Downloading $modelName ..."
    Invoke-WebRequest -Uri $url -OutFile $archivePath

    Write-Host "Extracting to $DestDir ..."
    tar -xf $archivePath -C $DestDir

    Remove-Item -LiteralPath $archivePath -Force
}

Write-Host ""
Write-Host "Done."
Write-Host "Model directory: $destPath"
Write-Host ""
Write-Host "Example arguments:"
Write-Host "  --kws-encoder $destPath\$($item.Encoder)"
Write-Host "  --kws-decoder $destPath\$($item.Decoder)"
Write-Host "  --kws-joiner  $destPath\$($item.Joiner)"
Write-Host "  --kws-tokens  $destPath\tokens.txt"
Write-Host ""
Write-Host "Note: zh-en 2025 model may require a newer sherpa-onnx/ORT build. If you see ORT API version errors, use -Model zh."
