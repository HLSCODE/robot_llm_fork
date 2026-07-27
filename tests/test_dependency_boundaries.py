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
    "src.arm_sdk",
    "src.base_move",
    "src.cameras.camera_factory",
    "src.devices",
    "src.expression_display.display",
)


class DependencyBoundaryTests(unittest.TestCase):
    def test_application_layers_do_not_import_concrete_hardware(self):
        violations: list[str] = []
        for relative_directory in APPLICATION_BOUNDARY_DIRECTORIES:
            for path in (PROJECT_ROOT / relative_directory).rglob("*.py"):
                module_name = ".".join(
                    path.relative_to(PROJECT_ROOT).with_suffix("").parts
                )
                package = module_name.rpartition(".")[0]
                tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
                for node in ast.walk(tree):
                    for imported in self._imported_modules(node, package):
                        if imported.startswith(FORBIDDEN_DEPENDENCIES):
                            violations.append(
                                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                                f"imports {imported}"
                            )

        self.assertEqual([], violations, "\n".join(violations))

    def test_application_layers_do_not_call_vendor_robot_api(self):
        violations: list[str] = []
        for relative_directory in APPLICATION_BOUNDARY_DIRECTORIES:
            for path in (PROJECT_ROOT / relative_directory).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
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


if __name__ == "__main__":
    unittest.main()
