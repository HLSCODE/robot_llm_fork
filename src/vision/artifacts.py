from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4
from types import TracebackType

from ..persistence.json_documents import write_json_atomic
from .models import VisionArtifact, VisionConfigurationVersion, VisionOperation

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_NAME = "manifest.json"


class VisionArtifactStore:
    """Own scoped debug runs and bounded retention under one configured root."""

    def __init__(
        self,
        root: str | Path,
        *,
        retention_days: int,
        max_runs: int,
        configuration: VisionConfigurationVersion,
    ) -> None:
        resolved = Path(root)
        if not resolved.is_absolute():
            resolved = _PROJECT_ROOT / resolved
        self._root = resolved.resolve()
        if self._root == _PROJECT_ROOT:
            raise ValueError("vision artifact root must not be the project root")
        if retention_days <= 0:
            raise ValueError("vision artifact retention_days must be positive")
        if max_runs <= 0:
            raise ValueError("vision artifact max_runs must be positive")
        self._retention_seconds = retention_days * 24 * 60 * 60
        self._max_runs = max_runs
        self._configuration = configuration

    @property
    def root(self) -> Path:
        return self._root

    def begin(self, operation: VisionOperation) -> VisionArtifactRun:
        self.cleanup()
        run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:12]}"
        operation_root = self._root / operation.value
        staging = operation_root / f".{run_id}.tmp"
        final = operation_root / run_id
        staging.mkdir(parents=True, exist_ok=False)
        return VisionArtifactRun(
            operation=operation,
            run_id=run_id,
            staging_directory=staging,
            final_directory=final,
            configuration=self._configuration,
        )

    def cleanup(self, *, now: float | None = None) -> None:
        if not self._root.exists():
            return
        current_time = time.time() if now is None else now
        completed: list[Path] = []
        for operation_root in self._root.iterdir():
            if not operation_root.is_dir():
                continue
            for run in operation_root.iterdir():
                if not run.is_dir():
                    continue
                age = current_time - run.stat().st_mtime
                is_stale_temporary = run.name.startswith(".") and age > 60 * 60
                if is_stale_temporary or age > self._retention_seconds:
                    shutil.rmtree(run)
                else:
                    completed.append(run)
        completed.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for expired in completed[self._max_runs :]:
            shutil.rmtree(expired)


@dataclass(slots=True)
class VisionArtifactRun(AbstractContextManager["VisionArtifactRun"]):
    operation: VisionOperation
    run_id: str
    staging_directory: Path
    final_directory: Path
    configuration: VisionConfigurationVersion
    _finished: bool = False

    def __enter__(self) -> VisionArtifactRun:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        self.finish(successful=exc_type is None)

    def finish(self, *, successful: bool) -> tuple[VisionArtifact, ...]:
        if self._finished:
            return self.artifacts()
        write_json_atomic(
            self.staging_directory / _MANIFEST_NAME,
            {
                "schema": "robot-llm.vision-artifacts",
                "schema_version": 1,
                "run_id": self.run_id,
                "operation": self.operation.value,
                "successful": successful,
                "configuration": self.configuration.to_dict(),
                "created_at": time.time(),
            },
        )
        os.replace(self.staging_directory, self.final_directory)
        self._finished = True
        return self.artifacts()

    def artifacts(self) -> tuple[VisionArtifact, ...]:
        directory = self.final_directory if self._finished else self.staging_directory
        if not directory.exists():
            return ()
        return tuple(
            VisionArtifact(kind=_artifact_kind(path), path=path)
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.name != _MANIFEST_NAME
        )


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".bmp"}:
        return "image"
    return "file"
