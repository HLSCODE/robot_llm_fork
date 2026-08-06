from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_BOUNDARY_DIRECTORIES = (
    "src/application",
    "src/data_collection",
    "src/gui",
    "src/robot_server",
    "src/execution",
    "src/vision",
    "src/voice_interaction",
)
FORBIDDEN_DEPENDENCIES = (
    "src.devices.robots.realman",
    "src.devices.motion",
    "src.devices.cameras",
    "src.devices.tools",
    "src.devices.displays",
    "src.devices.transports",
)
PRESENTATION_DIRECTORIES = (
    "src/gui",
    "src/robot_server",
    "src/voice_interaction",
)
LEGACY_PRESENTATION_MODULES = (
    "src/gui/main_window.py",
    "src/gui/device_view.py",
    "src/gui/workflow_view.py",
    "src/gui/dialogs.py",
    "src/gui/notifications.py",
    "src/gui/startup.py",
    "src/gui/view_models.py",
    "src/robot_server/handlers",
    "src/robot_server/access_control.py",
    "src/robot_server/request_limits.py",
    "src/robot_server/routing.py",
    "src/robot_server/transport_security.py",
    "src/robot_server/metrics.py",
    "src/robot_server/protocol.py",
)
REMOVED_ARCHITECTURE_PATHS = (
    "src/actions",
    "src/agents",
    "src/ai_integration",
    "src/core",
    "src/devices/tools/adapters.py",
    "src/devices/transports/devices",
    "src/vision/balance_reader_simple.py",
    "src/vision/pictures",
    "src/widgets",
)
STABLE_LAYER_DEPENDENCIES = {
    "src/domain": ("src.domain",),
    "src/configuration": ("src.configuration",),
    "src/persistence": ("src.persistence", "src.domain"),
    "src/geometry": ("src.geometry", "src.domain", "src.configuration"),
}
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
    "src/devices/cameras/camera_factory.py",
    "src/devices/motion/mobile_base/move_controller.py",
    "src/devices/motion/mobile_base/tcp_client.py",
    "src/application/localization.py",
    "src/execution/action_handlers.py",
    "src/vision/executor.py",
    "src/vision/capture.py",
    "src/vision/bottle_capture.py",
    "src/vision/catch.py",
    "src/vision/convert.py",
    "src/vision/crawl.py",
    "src/vision/interface.py",
    "src/vision/relocalization/cli.py",
)


class DependencyBoundaryTests(unittest.TestCase):
    def test_main_window_does_not_access_workflow_graphics_internals(self):
        path = PROJECT_ROOT / "src/gui/controllers/main_window.py"
        source = path.read_text(encoding="utf-8-sig")
        forbidden_members = (
            "topLevelItem",
            "QTreeWidgetItem",
            "_item_map",
            "_update_item_display",
            "_update_loop_display",
            "_find_item_by_entry",
        )
        remaining = [
            member for member in forbidden_members if member in source
        ]

        self.assertEqual([], remaining, "\n".join(remaining))

    def test_workflow_application_boundary_has_no_parallel_runtime(self):
        workflow_paths = (
            PROJECT_ROOT / "src/domain/workflow.py",
            PROJECT_ROOT / "src/application/workflow_validation.py",
            PROJECT_ROOT / "src/application/workflow_compiler.py",
            PROJECT_ROOT / "src/application/workflow_preflight.py",
        )
        forbidden_imports = ("src.gui", "src.devices")
        forbidden_classes = {
            "WorkflowExecutor",
            "NodeHandlerRegistry",
            "BaseNodeHandler",
        }
        violations: list[str] = []
        for path in workflow_paths:
            module_name = ".".join(
                path.relative_to(PROJECT_ROOT).with_suffix("").parts
            )
            package = module_name.rpartition(".")[0]
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
            for node in ast.walk(tree):
                for imported in self._imported_modules(node, package):
                    if imported.startswith(forbidden_imports):
                        violations.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                            f"imports {imported}"
                        )
                if isinstance(node, ast.ClassDef) and node.name in forbidden_classes:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                        f"defines forbidden parallel runtime {node.name}"
                    )

        self.assertEqual([], violations, "\n".join(violations))

    def test_transport_package_does_not_export_semantic_devices(self):
        from src.devices import transports

        semantic_exports = {
            "ElectricGripper",
            "StepperBus",
            "StepperMotor",
        }
        remaining = sorted(
            name for name in semantic_exports if hasattr(transports, name)
        )

        self.assertEqual([], remaining)

    def test_llm_registry_is_created_only_by_application_factory(self):
        creation_sites: list[str] = []
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                is_constructor = (
                    isinstance(function, ast.Name)
                    and function.id == "LLMRegistry"
                )
                is_factory = (
                    isinstance(function, ast.Attribute)
                    and function.attr == "from_settings"
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "LLMRegistry"
                )
                if is_constructor or is_factory:
                    creation_sites.append(
                        path.relative_to(PROJECT_ROOT).as_posix()
                    )

        self.assertEqual(["src/application/factory.py"], creation_sites)

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

    def test_presentation_layers_do_not_access_device_runtime(self):
        violations: list[str] = []
        for relative_directory in PRESENTATION_DIRECTORIES:
            for path in (PROJECT_ROOT / relative_directory).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Attribute) and node.attr == "device_runtime":
                        violations.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                            "accesses ApplicationServices.device_runtime"
                        )

        self.assertEqual([], violations, "\n".join(violations))

    def test_legacy_presentation_modules_are_removed(self):
        remaining = [
            relative_path
            for relative_path in LEGACY_PRESENTATION_MODULES
            if (PROJECT_ROOT / relative_path).exists()
        ]

        self.assertEqual([], remaining, "\n".join(remaining))

    def test_removed_architecture_paths_do_not_return(self):
        remaining = [
            relative_path
            for relative_path in REMOVED_ARCHITECTURE_PATHS
            if (PROJECT_ROOT / relative_path).exists()
        ]

        self.assertEqual([], remaining, "\n".join(remaining))

    def test_only_pyside6_qt_binding_is_allowed(self):
        forbidden_binding = "Py" + "Qt6"
        violations: list[str] = []
        for relative_directory in ("src", "scripts", "tests"):
            for path in (PROJECT_ROOT / relative_directory).rglob("*.py"):
                if path == Path(__file__).resolve():
                    continue
                if forbidden_binding in path.read_text(encoding="utf-8-sig"):
                    violations.append(
                        path.relative_to(PROJECT_ROOT).as_posix()
                    )

        project_configuration = (PROJECT_ROOT / "pyproject.toml").read_text(
            encoding="utf-8-sig"
        )
        if forbidden_binding in project_configuration:
            violations.append("pyproject.toml")

        self.assertEqual([], violations, "\n".join(violations))

    def test_stable_layers_have_one_way_dependencies(self):
        violations: list[str] = []
        for relative_directory, allowed_prefixes in STABLE_LAYER_DEPENDENCIES.items():
            for path in (PROJECT_ROOT / relative_directory).rglob("*.py"):
                module_name = ".".join(
                    path.relative_to(PROJECT_ROOT).with_suffix("").parts
                )
                package = module_name.rpartition(".")[0]
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
                for node in ast.walk(tree):
                    for imported in self._imported_modules(node, package):
                        if imported.startswith("src.") and not imported.startswith(
                            allowed_prefixes
                        ):
                            violations.append(
                                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                                f"imports {imported}"
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
