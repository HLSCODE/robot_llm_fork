"""Build the wheel and verify its installed console entry point."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import venv
from zipfile import ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_WHEEL_MEMBERS = {
    "src/__init__.py",
    "src/__main__.py",
    "src/bootstrap/launcher.py",
    "src/configuration/settings.py",
    "src/domain/models.py",
    "src/execution/workflows/powder_dispense.py",
    "src/gui/views/ai_assistant.py",
    "src/persistence/storage.py",
}
FORBIDDEN_WHEEL_PREFIXES = (
    "src/actions/",
    "src/agents/",
    "src/ai_integration/",
    "src/core/",
    "src/devices/transports/devices/",
    "src/vision/pictures/",
    "src/widgets/",
)
FORBIDDEN_WHEEL_MEMBERS: set[str] = set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and smoke-test the robot-llm wheel",
    )
    parser.add_argument(
        "--keep-artifact",
        action="store_true",
        help="also copy the verified wheel into dist/",
    )
    args = parser.parse_args(argv)

    with TemporaryDirectory(
        prefix=".package-smoke-",
        dir=PROJECT_ROOT,
    ) as temporary_directory:
        workspace = Path(temporary_directory)
        wheel = _build_wheel(workspace / "wheel")
        _validate_wheel_contents(wheel)
        _validate_installed_entry_point(wheel, workspace)
        if args.keep_artifact:
            destination = PROJECT_ROOT / "dist" / wheel.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(wheel, destination)
            print(f"Verified wheel copied to {destination}")
        else:
            print(f"Verified wheel: {wheel.name}")
    return 0


def _build_wheel(output_directory: Path) -> Path:
    output_directory.mkdir(parents=True)
    generated_directories = (
        PROJECT_ROOT / "build",
        PROJECT_ROOT / "robot_llm.egg-info",
    )
    preexisting_directories = {
        path for path in generated_directories if path.exists()
    }
    try:
        _run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(output_directory),
            ],
            cwd=PROJECT_ROOT,
        )
    finally:
        for path in generated_directories:
            if path not in preexisting_directories and path.is_dir():
                _remove_generated_directory(path)
    wheels = tuple(output_directory.glob("robot_llm-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {len(wheels)}")
    return wheels[0]


def _validate_wheel_contents(wheel: Path) -> None:
    with ZipFile(wheel) as archive:
        members = set(archive.namelist())
    missing = REQUIRED_WHEEL_MEMBERS - members
    if missing:
        raise RuntimeError(
            "wheel is missing required files: " + ", ".join(sorted(missing))
        )
    forbidden = FORBIDDEN_WHEEL_MEMBERS & members
    forbidden.update(
        member
        for member in members
        if member.startswith(FORBIDDEN_WHEEL_PREFIXES)
    )
    if forbidden:
        raise RuntimeError(
            "wheel contains removed historical files: "
            + ", ".join(sorted(forbidden))
        )


def _validate_installed_entry_point(wheel: Path, workspace: Path) -> None:
    environment_directory = workspace / "venv"
    venv.EnvBuilder(
        with_pip=True,
        system_site_packages=False,
    ).create(environment_directory)
    python = _venv_python(environment_directory)
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            str(wheel),
        ],
        cwd=workspace,
    )
    smoke_script = (
        "import importlib.metadata as metadata, sys; "
        "entry_points = [entry for entry in "
        "metadata.distribution('robot-llm').entry_points "
        "if entry.group == 'console_scripts' and entry.name == 'robot-llm']; "
        "assert len(entry_points) == 1, entry_points; "
        "sys.argv = ['robot-llm', '--check-config', '--simulation', "
        "'--disable-websocket']; "
        "raise SystemExit(entry_points[0].load()())"
    )
    _run(
        [str(python), "-c", smoke_script],
        cwd=workspace,
    )


def _venv_python(environment_directory: Path) -> Path:
    if sys.platform == "win32":
        return environment_directory / "Scripts" / "python.exe"
    return environment_directory / "bin" / "python"


def _remove_generated_directory(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != PROJECT_ROOT:
        raise RuntimeError(
            f"refusing to remove generated path outside project root: {resolved}"
        )
    shutil.rmtree(resolved)


def _run(command: list[str], *, cwd: Path) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
        )


if __name__ == "__main__":
    raise SystemExit(main())
