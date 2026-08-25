"""Typed initialization plans and side-effect execution services."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.request import urlopen

from ..configuration.config_initializer import initialize_configuration
from ..configuration.config_loader import load_application_settings
from ..configuration.config_validation import (
    ConfigurationSeverity,
    StartupOptions,
    validate_startup_configuration,
)
from ..configuration.settings import VoiceSettings


class InitializationStep(str, Enum):
    CONFIGURATION = "configuration"
    DEPENDENCIES = "dependencies"
    ASR_MODELS = "asr_models"
    KWS_MODEL = "kws_model"
    VALIDATION = "validation"

    @property
    def label(self) -> str:
        return {
            self.CONFIGURATION: "初始化配置",
            self.DEPENDENCIES: "同步依赖",
            self.ASR_MODELS: "准备 ASR / VAD 模型",
            self.KWS_MODEL: "下载 KWS 模型",
            self.VALIDATION: "校验配置",
        }[self]


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class EventKind(str, Enum):
    STATUS = "status"
    LOG = "log"


@dataclass(frozen=True, slots=True)
class InitializationPlan:
    project_root: Path
    steps: tuple[InitializationStep, ...]
    extras: tuple[str, ...] = ()
    kws_model: str = "zh-en"
    frozen: bool = True
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("初始化计划至少需要一个步骤")
        unknown_extras = set(self.extras) - SUPPORTED_EXTRAS
        if unknown_extras:
            rendered = ", ".join(sorted(unknown_extras))
            raise ValueError(f"未知依赖组: {rendered}")
        if self.kws_model not in KWS_MODELS:
            raise ValueError(f"不支持的 KWS 模型: {self.kws_model}")

    @property
    def ordered_steps(self) -> tuple[InitializationStep, ...]:
        selected = frozenset(self.steps)
        return tuple(step for step in STEP_ORDER if step in selected)


@dataclass(frozen=True, slots=True)
class InitializationEvent:
    kind: EventKind
    step: InitializationStep
    message: str
    status: StepStatus | None = None


@dataclass(frozen=True, slots=True)
class StepResult:
    step: InitializationStep
    status: StepStatus
    elapsed_seconds: float
    message: str


EventSink = Callable[[InitializationEvent], None]

SUPPORTED_EXTRAS = frozenset(
    {"gui", "server", "ai", "data", "vision", "hardware", "voice", "kws", "openwakeword"}
)
STEP_ORDER = (
    InitializationStep.CONFIGURATION,
    InitializationStep.DEPENDENCIES,
    InitializationStep.ASR_MODELS,
    InitializationStep.KWS_MODEL,
    InitializationStep.VALIDATION,
)


@dataclass(frozen=True, slots=True)
class KwsModelDefinition:
    name: str
    url: str
    encoder: str
    decoder: str
    joiner: str

    @property
    def required_files(self) -> tuple[str, ...]:
        return (self.encoder, self.decoder, self.joiner, "tokens.txt")


KWS_MODELS = {
    "zh-en": KwsModelDefinition(
        name="sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
            "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2"
        ),
        encoder="encoder-epoch-13-avg-2-chunk-16-left-64.onnx",
        decoder="decoder-epoch-13-avg-2-chunk-16-left-64.onnx",
        joiner="joiner-epoch-13-avg-2-chunk-16-left-64.onnx",
    ),
    "zh": KwsModelDefinition(
        name="sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
            "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2"
        ),
        encoder="encoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        decoder="decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        joiner="joiner-epoch-12-avg-2-chunk-16-left-64.onnx",
    ),
}

_FUNASR_MODELSCOPE_ALIASES = {
    "fsmn-vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "ct-punc": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
    "ct-punc-c": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
}


class InitializationRunner:
    """Execute a plan sequentially and publish structured progress events."""

    def __init__(self, event_sink: EventSink | None = None) -> None:
        self._event_sink = event_sink or (lambda event: None)
        self._cancel_requested = threading.Event()
        self._active_process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self._cancel_requested.set()
        process = self._active_process
        if process is not None and process.poll() is None:
            process.terminate()

    @property
    def is_cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def run(self, plan: InitializationPlan) -> tuple[StepResult, ...]:
        results: list[StepResult] = []
        has_failed = False
        for step in plan.ordered_steps:
            if has_failed or self._cancel_requested.is_set():
                result = StepResult(step, StepStatus.SKIPPED, 0.0, "前置步骤失败或任务已取消")
                results.append(result)
                self._status(result)
                continue
            result = self._run_step(plan, step)
            results.append(result)
            has_failed = result.status is StepStatus.FAILED
        return tuple(results)

    def _run_step(self, plan: InitializationPlan, step: InitializationStep) -> StepResult:
        self._event_sink(
            InitializationEvent(EventKind.STATUS, step, "正在执行", StepStatus.RUNNING)
        )
        started_at = time.perf_counter()
        try:
            if plan.dry_run:
                self._log(step, "演练模式：未执行任何写入、下载或依赖变更")
            else:
                self._step_handler(step)(plan, step)
        except Exception as exc:
            result = StepResult(
                step,
                StepStatus.FAILED,
                time.perf_counter() - started_at,
                str(exc),
            )
            self._log(step, f"失败：{exc}")
            self._status(result)
            return result
        result = StepResult(
            step,
            StepStatus.SUCCEEDED,
            time.perf_counter() - started_at,
            "完成",
        )
        self._status(result)
        return result

    def _step_handler(
        self, step: InitializationStep
    ) -> Callable[[InitializationPlan, InitializationStep], None]:
        return {
            InitializationStep.CONFIGURATION: self._initialize_configuration,
            InitializationStep.DEPENDENCIES: self._sync_dependencies,
            InitializationStep.ASR_MODELS: self._prepare_asr_models,
            InitializationStep.KWS_MODEL: self._download_kws_model,
            InitializationStep.VALIDATION: self._validate_configuration,
        }[step]

    def _initialize_configuration(
        self, plan: InitializationPlan, step: InitializationStep
    ) -> None:
        result = initialize_configuration(plan.project_root)
        for path in result.created:
            self._log(step, f"已创建 {path.relative_to(plan.project_root.resolve())}")
        for path in result.skipped:
            self._log(step, f"已保留 {path.relative_to(plan.project_root.resolve())}")
        self._log(step, f"创建 {len(result.created)} 个文件，保留 {len(result.skipped)} 个文件")

    def _sync_dependencies(self, plan: InitializationPlan, step: InitializationStep) -> None:
        command = ["uv", "sync"]
        if plan.frozen:
            command.append("--frozen")
        for extra in plan.extras:
            command.extend(("--extra", extra))
        self._log(step, f"执行：{' '.join(command)}")
        self._run_subprocess(command, cwd=plan.project_root, step=step)

    def _prepare_asr_models(self, plan: InitializationPlan, step: InitializationStep) -> None:
        script = plan.project_root / "scripts" / "test_download_asr_model.py"
        if not script.is_file():
            raise RuntimeError(f"ASR 模型初始化脚本不存在：{script}")
        self._log(step, "在可取消的独立进程中检查并准备语音模型")
        self._run_subprocess(
            [sys.executable, "-u", str(script)],
            cwd=plan.project_root,
            step=step,
        )

    def _download_kws_model(self, plan: InitializationPlan, step: InitializationStep) -> None:
        definition = KWS_MODELS[plan.kws_model]
        destination_root = plan.project_root / "models" / "kws"
        destination = destination_root / definition.name
        if _has_required_files(destination, definition.required_files):
            self._log(step, f"模型已完整存在：{destination}")
            return
        if destination.exists():
            raise RuntimeError(f"KWS 模型目录不完整，请人工确认后移除或修复：{destination}")

        destination_root.mkdir(parents=True, exist_ok=True)
        self._log(step, f"下载 {definition.name}")
        with tempfile.TemporaryDirectory(prefix="robot-kws-", dir=destination_root) as raw_temp:
            temporary_directory = Path(raw_temp)
            archive = temporary_directory / f"{definition.name}.tar.bz2"
            _download_file(
                definition.url,
                archive,
                cancel_requested=self._cancel_requested,
                progress=lambda message: self._log(step, message),
            )
            self._log(step, "校验并解压模型")
            _extract_archive_safely(archive, temporary_directory)
            extracted = temporary_directory / definition.name
            if not _has_required_files(extracted, definition.required_files):
                raise RuntimeError("下载的 KWS 模型缺少必要文件")
            shutil.move(str(extracted), str(destination))
        self._log(step, f"模型已保存：{destination}")

    def _validate_configuration(self, plan: InitializationPlan, step: InitializationStep) -> None:
        settings = load_application_settings(plan.project_root / "config" / "config.toml")
        report = validate_startup_configuration(
            settings,
            StartupOptions(
                simulation=True,
                websocket_enabled=False,
                websocket_host="127.0.0.1",
                websocket_port=8765,
                log_level="INFO",
            ),
        )
        for issue in report.issues:
            prefix = "错误" if issue.severity is ConfigurationSeverity.ERROR else "警告"
            self._log(step, f"{prefix} [{issue.code}] {issue.field}: {issue.message}")
        if report.errors:
            raise ValueError(f"配置校验发现 {len(report.errors)} 个错误")
        self._log(step, f"校验通过，警告 {len(report.warnings)} 个")

    def _run_subprocess(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        step: InitializationStep,
    ) -> None:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"未找到可执行程序：{command[0]}") from exc
        self._active_process = process
        try:
            assert process.stdout is not None
            for line in process.stdout:
                self._log(step, line.rstrip())
                if self._cancel_requested.is_set():
                    process.terminate()
                    break
            return_code = process.wait()
        finally:
            self._active_process = None
        if return_code != 0:
            raise RuntimeError(f"命令执行失败，退出码 {return_code}")

    def _log(self, step: InitializationStep, message: str) -> None:
        self._event_sink(InitializationEvent(EventKind.LOG, step, message))

    def _status(self, result: StepResult) -> None:
        self._event_sink(
            InitializationEvent(EventKind.STATUS, result.step, result.message, result.status)
        )


def _has_required_files(directory: Path, required_files: Iterable[str]) -> bool:
    return directory.is_dir() and all((directory / name).is_file() for name in required_files)


def prepare_asr_models(
    settings: VoiceSettings,
    *,
    include_vad: bool = True,
    include_asr: bool = True,
    log: Callable[[str], None] | None = None,
    cancel_requested: threading.Event | None = None,
) -> None:
    """Ensure configured speech models exist in the local provider cache."""
    report = log or (lambda message: None)
    cancellation = cancel_requested or threading.Event()
    vad_model = _resolve_cached_funasr_model(settings.voice_vad_model)
    asr_model = _resolve_cached_funasr_model(settings.voice_asr_model)
    punc_model = (
        _resolve_cached_funasr_model(settings.voice_asr_punc_model)
        if settings.voice_asr_punc_model
        else None
    )
    has_cached_asr_bundle = asr_model is not None and (
        not settings.voice_asr_punc_model or punc_model is not None
    )
    needs_vad_download = include_vad and vad_model is None
    needs_asr_download = include_asr and not has_cached_asr_bundle
    if needs_vad_download or needs_asr_download:
        try:
            from ..voice_interaction.speech.asr import FunASRRecognizer
            from ..voice_interaction.speech.vad import FunASRVAD
        except ImportError as exc:
            raise ImportError("缺少语音依赖，请同时选择 voice 依赖组") from exc

    if cancellation.is_set():
        raise RuntimeError("初始化已取消")
    if include_vad and vad_model is not None:
        report(f"VAD 模型已存在，跳过下载：{vad_model}")
    elif include_vad:
        report(f"下载 VAD 模型：{settings.voice_vad_model}")
        FunASRVAD(
            model=settings.voice_vad_model,
            chunk_size_ms=settings.voice_vad_chunk_ms,
            suppress_model_output=settings.voice_suppress_model_output,
        )
    if cancellation.is_set():
        raise RuntimeError("初始化已取消")
    if include_asr and has_cached_asr_bundle:
        report(f"ASR 模型已存在，跳过下载：{asr_model}")
        if punc_model is not None:
            report(f"标点模型已存在，跳过下载：{punc_model}")
    elif include_asr:
        report(f"下载 ASR 模型：{settings.voice_asr_model}")
        FunASRRecognizer(
            model=str(asr_model or settings.voice_asr_model),
            punc_model=(
                str(punc_model or settings.voice_asr_punc_model)
                if settings.voice_asr_punc_model
                else None
            ),
            device=settings.voice_asr_device or None,
            batch_size_s=settings.voice_asr_batch_size_s,
            suppress_model_output=settings.voice_suppress_model_output,
        )


def _resolve_cached_funasr_model(model: str) -> Path | None:
    """Resolve a local FunASR/ModelScope model without performing network I/O."""
    normalized = model.strip()
    if not normalized:
        return None
    direct_path = Path(normalized).expanduser()
    if _is_complete_funasr_model(direct_path):
        return direct_path.resolve()

    model_id = _FUNASR_MODELSCOPE_ALIASES.get(normalized, normalized)
    legacy_cached = _legacy_modelscope_cache_path(model_id)
    if legacy_cached is not None:
        return legacy_cached.resolve()

    try:
        from funasr.download.name_maps_from_hub import name_maps_ms

        model_id = name_maps_ms.get(normalized, model_id)
    except ImportError:
        pass
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError:
        return None

    cached: Path | None
    try:
        cached = Path(
            snapshot_download(
                model_id,
                revision="master",
                local_files_only=True,
            )
        )
    except Exception:
        cached = _legacy_modelscope_cache_path(model_id)
    return cached.resolve() if cached is not None and _is_complete_funasr_model(cached) else None


def _legacy_modelscope_cache_path(model_id: str) -> Path | None:
    cache_root = Path(
        os.environ.get("MODELSCOPE_CACHE", Path.home() / ".cache" / "modelscope")
    ).expanduser()
    repository_name = model_id.replace("/", "--")
    candidates = (
        cache_root / "models" / repository_name / "snapshots" / "master",
        cache_root / "models" / Path(model_id) / "snapshots" / "master",
        cache_root / "hub" / Path(model_id),
    )
    return next((path for path in candidates if _is_complete_funasr_model(path)), None)


def _is_complete_funasr_model(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_configuration = (path / "configuration.json").is_file() or (
        path / "config.yaml"
    ).is_file()
    has_weights = any(
        candidate.is_file()
        for pattern in ("*.pt", "*.bin", "*.onnx", "*.safetensors")
        for candidate in path.glob(pattern)
    )
    return has_configuration and has_weights


def _download_file(
    url: str,
    destination: Path,
    *,
    cancel_requested: threading.Event,
    progress: Callable[[str], None],
) -> None:
    with urlopen(url, timeout=30) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length", "0"))
        downloaded = 0
        last_reported = -1
        while chunk := response.read(1024 * 1024):
            if cancel_requested.is_set():
                raise RuntimeError("初始化已取消")
            output.write(chunk)
            downloaded += len(chunk)
            if total:
                percentage = int(downloaded * 100 / total)
                if percentage >= last_reported + 5:
                    progress(f"下载进度 {percentage}%")
                    last_reported = percentage


def _extract_archive_safely(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, mode="r:bz2") as bundle:
        bundle.extractall(destination, filter="data")
