#!/usr/bin/env bash
set -euo pipefail

dest_dir="${1:-models/kws}"
model="${2:-zh-en}"

case "$model" in
  zh-en)
    model_name="sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
    encoder="encoder-epoch-13-avg-2-chunk-16-left-64.onnx"
    decoder="decoder-epoch-13-avg-2-chunk-16-left-64.onnx"
    joiner="joiner-epoch-13-avg-2-chunk-16-left-64.onnx"
    ;;
  zh)
    model_name="sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
    encoder="encoder-epoch-12-avg-2-chunk-16-left-64.onnx"
    decoder="decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
    joiner="joiner-epoch-12-avg-2-chunk-16-left-64.onnx"
    ;;
  *)
    echo "Unknown model: $model" >&2
    echo "Usage: $0 [dest_dir] [zh-en|zh]" >&2
    exit 2
    ;;
esac

url="https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/${model_name}.tar.bz2"
mkdir -p "$dest_dir"

dest_path="$dest_dir/$model_name"
archive_path="$dest_dir/$model_name.tar.bz2"

if [ -d "$dest_path" ]; then
  echo "Model already exists: $dest_path"
else
  echo "Downloading $model_name ..."
  if command -v curl >/dev/null 2>&1; then
    curl -L "$url" -o "$archive_path"
  elif command -v wget >/dev/null 2>&1; then
    wget "$url" -O "$archive_path"
  else
    echo "Please install curl or wget." >&2
    exit 1
  fi

  echo "Extracting to $dest_dir ..."
  tar -xf "$archive_path" -C "$dest_dir"
  rm -f "$archive_path"
fi

cat <<EOF

Done.
Model directory: $dest_path

Example arguments:
  --kws-encoder $dest_path/$encoder
  --kws-decoder $dest_path/$decoder
  --kws-joiner  $dest_path/$joiner
  --kws-tokens  $dest_path/tokens.txt

Note: zh-en 2025 model may require a newer sherpa-onnx/ORT build. If you see ORT API version errors, use model "zh".
EOF
