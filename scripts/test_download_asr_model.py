from __future__ import annotations

import argparse
import logging
import time


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download and load configured FunASR VAD/ASR models."
    )
    parser.add_argument("--asr-model", default=None, help="FunASR ASR model name")
    parser.add_argument("--vad-model", default=None, help="FunASR VAD model name")
    parser.add_argument("--punc-model", default=None, help="FunASR punctuation model; empty disables it")
    parser.add_argument("--device", default=None, help="FunASR device, for example cpu or cuda:0")
    parser.add_argument("--show-model-output", action="store_true", help="do not suppress model output")
    parser.add_argument("--skip-vad", action="store_true", help="only load ASR model")
    parser.add_argument("--skip-asr", action="store_true", help="only load VAD model")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    from src.core.config_loader import load_application_settings
    from src.voice_interaction.speech.asr import FunASRRecognizer
    from src.voice_interaction.speech.vad import FunASRVAD

    voice_config = load_application_settings().voice.as_runtime_mapping()
    suppress_model_output = not args.show_model_output

    if not args.skip_vad:
        vad_model = args.vad_model or voice_config.get("vad_model") or "fsmn-vad"
        started_at = time.perf_counter()
        print(f"Loading VAD model: {vad_model}")
        FunASRVAD(
            model=vad_model,
            chunk_size_ms=int(voice_config.get("vad_chunk_ms") or 200),
            suppress_model_output=suppress_model_output,
        )
        print(f"VAD ready, elapsed={(time.perf_counter() - started_at):.2f}s")

    if not args.skip_asr:
        asr_model = args.asr_model or voice_config.get("asr_model") or "iic/SenseVoiceSmall"
        punc_model = args.punc_model
        if punc_model is None:
            punc_model = voice_config.get("asr_punc_model") or None
        elif punc_model == "":
            punc_model = None
        device = args.device if args.device is not None else voice_config.get("asr_device") or None

        started_at = time.perf_counter()
        print(f"Loading ASR model: {asr_model}")
        if punc_model:
            print(f"Loading punctuation model: {punc_model}")
        FunASRRecognizer(
            model=asr_model,
            punc_model=punc_model,
            device=device,
            batch_size_s=int(voice_config.get("asr_batch_size_s") or 60),
            suppress_model_output=False,
        )
        print(f"ASR ready, elapsed={(time.perf_counter() - started_at):.2f}s")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
