"""Build a fresh Windows portable distribution without copying local user data."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_NAME = "robot-llm-windows-x86_64"


def shorten_license_paths(
    entries: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    """Preserve wheel licenses without exceeding Windows extraction path limits."""
    shortened: list[tuple[str, str, str]] = []
    original_paths: dict[str, str] = {}
    for destination, source, kind in entries:
        normalized = destination.replace("\\", "/")
        if ".dist-info/licenses/" in normalized:
            distribution = normalized.split(".dist-info/", 1)[0]
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
            destination = f"_licenses/{distribution}/{digest}-{Path(normalized).name}"
            original_paths[destination] = normalized
        shortened.append((destination, source, kind))
    return shortened, original_paths


def copy_release_templates(source: Path, destination: Path) -> None:
    """Explicit allowlist: never recursively copy local config/data/models."""
    templates = [source / ".env.example", source / "config/config.example.toml"]
    templates.extend(sorted((source / "config/fragments").rglob("*.example.toml")))
    templates.extend(
        source / "models/kws" / name
        for name in ("README.md", "keywords.txt", "keywords_raw.txt", "keywords.example.txt")
        if (source / "models/kws" / name).is_file()
    )
    for template in templates:
        target = destination / template.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template, target)
    shutil.copy2(source / "docs/windows-portable.md", destination / "README.md")
    for name in ("data", "logs", "models"):
        (destination / name).mkdir(exist_ok=True)


def smoke_check(bundle: Path) -> None:
    """Exercise frozen imports and configuration from an unrelated working directory."""
    with tempfile.TemporaryDirectory(prefix="robot-portable-smoke-") as temporary:
        sandbox = Path(temporary)
        # Test configuration in isolation; the published bundle stays uninitialized.
        copy_release_templates(PROJECT_ROOT, sandbox)
        subprocess.run(
            [str(bundle / "robot-init.exe"), "--non-interactive", "--project-root",
             str(sandbox), "--steps", "configuration,validation"],
            cwd=sandbox, check=True, timeout=120,
        )
        subprocess.run(
            [str(bundle / "robot-init.exe"), "--portable-smoke-check"],
            cwd=sandbox, check=True, timeout=120,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "dist")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        parser.error("请在 Windows x86_64 上构建")
    version = importlib.metadata.version("robot-llm")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    release = output / f"robot-llm-{version}-windows-x86_64"
    archive = Path(f"{release}.zip")
    if release.exists() or archive.exists():
        parser.error(f"发布目标已存在，请指定新的 --output：{release}")
    # Fresh staging prevents old configs or secrets leaking into a later release.
    with tempfile.TemporaryDirectory(prefix="robot-package-", dir=output) as temporary:
        staging = Path(temporary)
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
             "--distpath", str(staging / "dist"), "--workpath", str(staging / "build"),
             str(PROJECT_ROOT / "packaging/windows/robot-llm.spec")],
            cwd=PROJECT_ROOT, check=True,
        )
        bundle = staging / "dist" / BUNDLE_NAME
        copy_release_templates(PROJECT_ROOT, bundle)
        smoke_check(bundle)
        shutil.move(str(bundle), str(release))
    shutil.make_archive(str(release), "zip", root_dir=output, base_dir=release.name)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    Path(f"{archive}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"便携包：{archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
