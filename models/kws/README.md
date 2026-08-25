# KWS Models

Place sherpa-onnx keyword spotting model files here, for example:

```text
models/kws/sherpa-onnx-kws-xxx/
  encoder.onnx
  decoder.onnx
  joiner.onnx
  tokens.txt
```

Create `models/kws/keywords_raw.txt` with human-readable keywords, then
generate `models/kws/keywords.txt` with the matching model tokenization tool.
Do not write Chinese characters directly into `keywords.txt` for the zh-en
model; it expects phone/pinyin tokens.

From the project root:

```powershell
.\scripts\generate_kws_keywords.ps1
```

```bash
bash scripts/generate_kws_keywords.sh
```

Quick download from the project root:

```powershell
.\scripts\download_kws_model.ps1
```

```bash
bash scripts/download_kws_model.sh
```
