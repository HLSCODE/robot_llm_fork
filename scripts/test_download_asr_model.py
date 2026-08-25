from __future__ import annotations

import argparse
from dataclasses import replace
import logging


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

    from src.bootstrap.initialization import prepare_asr_models
    from src.configuration.config_loader import load_application_settings

    configured = load_application_settings().voice
    voice_settings = replace(
        configured,
        voice_vad_model=args.vad_model or configured.voice_vad_model,
        voice_asr_model=args.asr_model or configured.voice_asr_model,
        voice_asr_punc_model=(
            configured.voice_asr_punc_model if args.punc_model is None else args.punc_model
        ),
        voice_asr_device=(
            configured.voice_asr_device if args.device is None else args.device
        ),
        voice_suppress_model_output=not args.show_model_output,
    )
    prepare_asr_models(
        voice_settings,
        include_vad=not args.skip_vad,
        include_asr=not args.skip_asr,
        log=print,
    )

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
