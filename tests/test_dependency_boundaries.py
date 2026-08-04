from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_BOUNDARY_DIRECTORIES = (
    "src/actions",
    "src/agents",
    "src/application",
    "src/data_collection",
    "src/gui",
    "src/widgets",
    "src/ai_integration",
    "src/robot_server",
    "src/execution",
    "src/vision",
    "src/voice_interaction",
)
FORBIDDEN_DEPENDENCIES = (
    "src.devices.robots.realman",
    "src.devices.motion",
    "src.devices.cameras.camera_factory",
    "src.devices.tools",
    "src.devices.displays",
    "src.devices.transports",
)
PRESENTATION_DIRECTORIES = (
    "src/gui",
    "src/widgets",
    "src/ai_integration",
    "src/robot_server",
    "src/voice_interaction",
)
FORBIDDEN_RUNTIME_OPERATIONS = (
    "initialize",
    "require",
    "shutdown",
    "shutdown_all",
)
LEGACY_HARDWARE_PATHS = (
    "src/arm_sdk",
    "src/base_move",
    "src/cameras",
    "src/device_control_sdk",
    "src/device_runtime",
    "src/expression_display",
    "src/pwm_sdk",
    "src/devices/adp.py",
    "src/devices/kuaihuanshou.py",
    "src/devices/modbus_motor.py",
    "src/devices/pwm_neck.py",
    "src/devices/relay.py",
    "src/devices/tapping_controller.py",
)


class DependencyBoundaryTests(unittest.TestCase):
    def test_legacy_hardware_locations_are_removed(self):
        remaining = [
            relative_path
            for relative_path in LEGACY_HARDWARE_PATHS
            if (PROJECT_ROOT / relative_path).exists()
        ]

        self.assertEqual([], remaining, "\n".join(remaining))

    def test_application_layers_do_not_import_concrete_hardware(self):
        violations: list[str] = []
        for relative_directory in APPLICATION_BOUNDARY_DIRECTORIES:
            for path in (PROJECT_ROOT / relative_directory).rglob("*.py"):
                module_name = ".".join(
                    path.relative_to(PROJECT_ROOT).with_suffix("").parts
                )
                package = module_name.rpartition(".")[0]
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
                for node in ast.walk(tree):
                    for imported in self._imported_modules(node, package):
                        if imported.startswith(FORBIDDEN_DEPENDENCIES):
                            violations.append(
                                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                                f"imports {imported}"
                            )

        self.assertEqual([], violations, "\n".join(violations))

    def test_realman_sdk_is_confined_to_realman_directory(self):
        violations: list[str] = []
        realman_root = PROJECT_ROOT / "src/devices/robots/realman"
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            if path.is_relative_to(realman_root):
                continue
            module_name = ".".join(
                path.relative_to(PROJECT_ROOT).with_suffix("").parts
            )
            package = module_name.rpartition(".")[0]
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
            for node in ast.walk(tree):
                for imported in self._imported_modules(node, package):
                    if imported == "Robotic_Arm" or imported.startswith(
                        "Robotic_Arm."
                    ):
                        violations.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                            f"imports {imported}"
                        )

        self.assertEqual([], violations, "\n".join(violations))

    def test_realman_adapter_does_not_call_vendor_sdk(self):
        adapter_path = (
            PROJECT_ROOT / "src/devices/robots/realman/adapter.py"
        )
        tree = ast.parse(
            adapter_path.read_text(encoding="utf-8-sig"),
            str(adapter_path),
        )
        violations = [
            f"{adapter_path.relative_to(PROJECT_ROOT)}:{node.lineno} "
            f"accesses vendor member {node.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and (
                node.attr.startswith("rm_")
                or node.attr in {"robot1_ctrl", "robot2_ctrl"}
            )
        ]

        self.assertEqual([], violations, "\n".join(violations))

    def test_application_layers_do_not_call_vendor_robot_api(self):
        violations: list[str] = []
        for relative_directory in APPLICATION_BOUNDARY_DIRECTORIES:
            for path in (PROJECT_ROOT / relative_directory).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Attribute):
                        continue
                    if node.attr.startswith("rm_") or node.attr in {
                        "robot1_ctrl",
                        "robot2_ctrl",
                    }:
                        violations.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                            f"accesses vendor API {node.attr}"
                        )

        self.assertEqual([], violations, "\n".join(violations))

    def test_presentation_layers_do_not_control_device_runtime_directly(self):
        violations: list[str] = []
        forbidden_suffixes = tuple(
            f".device_runtime.{operation}"
            for operation in FORBIDDEN_RUNTIME_OPERATIONS
        )
        for relative_directory in PRESENTATION_DIRECTORIES:
            for path in (PROJECT_ROOT / relative_directory).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    call_path = self._attribute_path(node.func)
                    if call_path.endswith(forbidden_suffixes):
                        violations.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                            f"calls {call_path}"
                        )

        self.assertEqual([], violations, "\n".join(violations))

    @staticmethod
    def _imported_modules(node: ast.AST, package: str) -> tuple[str, ...]:
        if isinstance(node, ast.Import):
            return tuple(alias.name for alias in node.names)
        if not isinstance(node, ast.ImportFrom):
            return ()
        if node.level == 0:
            return (node.module or "",)
        relative_name = "." * node.level + (node.module or "")
        return (resolve_name(relative_name, package),)

    @classmethod
    def _attribute_path(cls, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = cls._attribute_path(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""


if __name__ == "__main__":
    unittest.main()
