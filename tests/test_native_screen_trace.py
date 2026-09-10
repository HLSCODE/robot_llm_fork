"""Validate opt-in tracing without starting a debugger or touching hardware."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/debugger/qt_screen_lifecycle.cdb"
WATCHER = ROOT / "scripts/watch_qt_screens.ps1"


def test_native_template_tracks_lifetime_and_only_dumps_unhandled_errors() -> None:
    commands = TEMPLATE.read_text(encoding="utf-8")
    breakpoints = [line for line in commands.splitlines() if line.startswith("bu ")]
    assert len(breakpoints) == 8
    assert all("@$tpid" in line and "@$tid" in line for line in breakpoints)
    assert all(".time; k 0n48; gc" in line for line in breakpoints)
    assert "bu Qt6Gui!QScreen::~QScreen" in commands
    assert "bu Qt6Gui!QScreen::QScreen" in commands
    assert "bu Qt6Core!QCoreApplication::aboutToQuit" in commands
    assert "bu Qt6Gui!QGuiApplication::~QGuiApplication" in commands
    assert ".outmask- /l" in commands  # retain log output when console is muted
    exceptions = [line for line in commands.splitlines() if line.startswith("sxd ")]
    assert len(exceptions) == 4
    assert all('-c2 "' in line and "; gn" in line for line in exceptions)
    assert all(".dump /ma /u" in line for line in exceptions)
    assert [line for line in commands.splitlines() if line.startswith("sxe ")] == [
        'sxe -c "$$<@@COMMAND_PATH@@" ibp',
        'sxe -c "$$<@@VTABLE_COMMAND_PATH@@; g" ld:Qt6Gui.dll',
    ]
    assert "QScreen::geometry" not in commands  # no breakpoint on a hot drawing path
    assert '.foreach (qtmodule {lm 1m m Qt6Gui})' in commands
    assert commands.index('.outmask /l 1') < commands.index('.foreach (qtmodule')
    assert commands.rstrip().endswith('.outmask- /l 1\ng')


def test_virtual_destructor_uses_validated_abi_not_a_version_specific_code_offset() -> None:
    commands = (TEMPLATE.parent / "qt_screen_vtable.cdb").read_text(encoding="utf-8")
    for method in ("metaObject", "qt_metacast", "qt_metacall"):
        assert f"== Qt6Gui!QScreen::{method}" in commands
    assert 'r @$t19 = @!"Qt6Gui!QScreen::`vftable\'"' in commands
    assert "bp poi(@$t19+0x18)" in commands  # MSVC x64 ABI slot, not a DLL offset
    assert "QT_SCREEN_VTABLE_UNSUPPORTED" in commands
    assert "QT_SCREEN_VIRTUAL_DTOR_ARMED" in commands
    assert "QObject::~QObject" not in commands  # don't trap every QObject deletion


@pytest.fixture
def powershell() -> str:
    if sys.platform != "win32":
        pytest.skip("Windows PowerShell wrapper; template checks run on all platforms")
    executable = shutil.which("powershell.exe")
    if executable is None:
        pytest.skip("PowerShell is not installed")
    return executable


@pytest.fixture
def cdb_stub(tmp_path: Path) -> Path:
    # DryRun only reads the PE header; this is deliberately not executable code.
    path = tmp_path / "debugger with spaces" / "cdb.exe"
    path.parent.mkdir()
    header = bytearray(128)
    struct.pack_into("<H", header, 0, 0x5A4D)
    struct.pack_into("<I", header, 0x3C, 64)
    struct.pack_into("<IH", header, 64, 0x4550, 0x8664)
    path.write_bytes(header)
    return path


def preview(powershell: str, cdb: Path, output: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [powershell, "-NoProfile", "-File", str(WATCHER),
         "-CdbPath", str(cdb), "-OutputDirectory", str(output), "-DryRun", *arguments],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )


@pytest.mark.parametrize("mode", ["Probe", "Simulation", "Hardware"])
def test_launch_preview_has_no_writes_or_debugger_execution(
    powershell: str, cdb_stub: Path, tmp_path: Path, mode: str,
) -> None:
    output = tmp_path / "trace with spaces"
    result = preview(powershell, cdb_stub, output, "-Launch", mode,
                     "-PythonPath", sys.executable)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    args = plan["arguments"]
    assert "-pd" in args and "-hd" in args and "-G" in args
    assert "-g" not in args  # don't skip installing startup breakpoints
    assert "-o" in args  # follow Windows venv launcher child
    assert ("--simulation" in args) is (mode == "Simulation")
    if mode == "Probe":
        assert "src.bootstrap.launcher" not in args
        assert Path(args[-1]).name == "qt_screen_lifecycle.py"
    assert "@@DUMP_PATH@@" not in plan["commands"]
    assert "@@COMMAND_PATH@@" not in plan["commands"]
    assert "@@VTABLE_COMMAND_PATH@@" not in plan["commands"]
    assert output.as_posix() in plan["commands"]
    assert not output.exists()


@pytest.mark.parametrize("name", ['bad;path', 'bad$path', 'bad`path', '中文路径'])
def test_rejects_paths_that_cannot_safely_enter_cdb_command_file(
    powershell: str, cdb_stub: Path, tmp_path: Path, name: str,
) -> None:
    result = preview(powershell, cdb_stub, tmp_path / name,
                     "-Launch", "Probe", "-PythonPath", sys.executable)
    assert result.returncode != 0
    assert "OutputDirectory must be an ASCII path" in result.stderr


def test_rejects_wrong_debugger_architecture(
    powershell: str, cdb_stub: Path, tmp_path: Path,
) -> None:
    header = bytearray(cdb_stub.read_bytes())
    struct.pack_into("<H", header, 68, 0x14C)
    cdb_stub.write_bytes(header)
    result = preview(powershell, cdb_stub, tmp_path / "output", "-Launch", "Probe")
    assert result.returncode != 0
    assert "Windows x64" in result.stderr


def test_attach_requires_explicit_pid_and_does_not_follow_other_processes(
    powershell: str, cdb_stub: Path, tmp_path: Path,
) -> None:
    import os

    result = preview(powershell, cdb_stub, tmp_path / "output",
                     "-TargetProcessId", str(os.getpid()))
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)["arguments"]
    assert args[-2:] == ["-p", str(os.getpid())]
    assert "-o" not in args
    assert not (tmp_path / "output").exists()


def test_declining_confirmation_does_not_create_session(
    powershell: str, cdb_stub: Path, tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(WATCHER), "-CdbPath", str(cdb_stub),
         "-OutputDirectory", str(output), "-Launch", "Probe", "-PythonPath", sys.executable],
        input="NO\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert not output.exists()


def test_probe_rejects_application_config(
    powershell: str, cdb_stub: Path, tmp_path: Path,
) -> None:
    result = preview(powershell, cdb_stub, tmp_path / "output", "-Launch", "Probe",
                     "-PythonPath", sys.executable, "-ConfigPath", "irrelevant.toml")
    assert result.returncode != 0
    assert "not applicable" in result.stderr


def test_default_paths_are_resolved_in_script_not_callers_working_directory(
    powershell: str, cdb_stub: Path, tmp_path: Path,
) -> None:
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(WATCHER), "-CdbPath", str(cdb_stub),
         "-Launch", "Probe", "-DryRun", "-PythonPath", sys.executable],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert Path(plan["session_directory"]).parent == ROOT / "logs/crash/native"
