#!/usr/bin/env bash
set -euo pipefail

model_dir="${1:-models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20}"
input="${2:-models/kws/keywords_raw.txt}"
output="${3:-models/kws/keywords.txt}"
tokens_type="${4:-phone+ppinyin}"

tokens="$model_dir/tokens.txt"
lexicon="$model_dir/en.phone"

if [ ! -f "$tokens" ]; then
  echo "tokens.txt not found: $tokens" >&2
  exit 1
fi
if [ ! -f "$input" ]; then
  echo "raw keywords file not found: $input" >&2
  exit 1
fi

args=(
  run
  sherpa-onnx-cli
  text2token
  --tokens "$tokens"
  --tokens-type "$tokens_type"
)

if [ "$tokens_type" = "phone+ppinyin" ]; then
  if [ ! -f "$lexicon" ]; then
    echo "lexicon not found: $lexicon" >&2
    exit 1
  fi
  args+=(--lexicon "$lexicon")
fi

args+=("$input" "$output")

uv "${args[@]}"
echo "Generated KWS keywords: $output"

