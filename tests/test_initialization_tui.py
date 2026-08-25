from __future__ import annotations

import asyncio
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from src.bootstrap.initialization import InitializationPlan, InitializationStep
from src.bootstrap.initialization_tui import (
    ChoiceList,
    InitializationApp,
    StepItem,
    WizardStage,
)


class InitializationTuiTests(unittest.TestCase):
    def test_keyboard_wizard_and_collapsed_execution_details(self) -> None:
        asyncio.run(self._exercise_keyboard_wizard())

    async def _exercise_keyboard_wizard(self) -> None:
        plan = InitializationPlan(
            project_root=Path.cwd(),
            steps=(InitializationStep.CONFIGURATION,),
            extras=(),
            dry_run=True,
        )
        app = InitializationApp(plan)

        async with app.run_test(size=(100, 36)) as pilot:
            self.assertEqual(WizardStage.STEPS, app._stage)
            self.assertEqual(
                (InitializationStep.CONFIGURATION.value,),
                app.query_one("#steps-list", ChoiceList).selected_ids,
            )

            await pilot.press("ctrl+a")
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(WizardStage.EXTRAS, app._stage)

            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(WizardStage.KWS_MODEL, app._stage)

            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(WizardStage.REVIEW, app._stage)

            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(WizardStage.EXECUTION, app._stage)

            items = tuple(app.query(StepItem))
            visible_items = tuple(item for item in items if item.display)
            self.assertTrue(visible_items)
            self.assertTrue(all(not item.detail.display for item in visible_items))

            visible_items[0].focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertTrue(visible_items[0].detail.display)

    def test_optional_pages_are_skipped_when_not_selected(self) -> None:
        asyncio.run(self._exercise_short_route())

    async def _exercise_short_route(self) -> None:
        plan = InitializationPlan(
            project_root=Path.cwd(),
            steps=(InitializationStep.CONFIGURATION, InitializationStep.VALIDATION),
            extras=(),
            dry_run=True,
        )
        app = InitializationApp(plan)

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(WizardStage.REVIEW, app._stage)

    def test_ctrl_c_has_priority_over_focused_child_widget(self) -> None:
        asyncio.run(self._exercise_ctrl_c())

    async def _exercise_ctrl_c(self) -> None:
        plan = InitializationPlan(
            project_root=Path.cwd(),
            steps=(InitializationStep.CONFIGURATION,),
            dry_run=True,
        )
        app = InitializationApp(plan)

        async with app.run_test(size=(100, 30)) as pilot:
            runner = Mock()
            app._runner = runner
            app._is_executing = True
            app._show_stage(WizardStage.EXECUTION, animate=False)

            await pilot.press("ctrl+c")
            await pilot.pause()

            self.assertTrue(app._cancel_pending)
            runner.cancel.assert_called()

    def test_step_details_can_be_copied_while_collapsed(self) -> None:
        asyncio.run(self._exercise_copy_details())

    async def _exercise_copy_details(self) -> None:
        plan = InitializationPlan(
            project_root=Path.cwd(),
            steps=(InitializationStep.CONFIGURATION,),
            dry_run=True,
        )
        app = InitializationApp(plan)

        async with app.run_test(size=(100, 30)) as pilot:
            item = app.query_one("#step-configuration", StepItem)
            item.display = True
            item.detail.display = False
            item.write_log("first line")
            item.write_log("second line")
            item.focus()

            with patch.object(app, "copy_to_clipboard") as copy:
                await pilot.press("c")
                await pilot.pause()

            copy.assert_called_once_with("first line\nsecond line")
            self.assertFalse(item.detail.display)


if __name__ == "__main__":
    unittest.main()
