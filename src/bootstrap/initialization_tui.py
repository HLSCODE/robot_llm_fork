"""Minimal Textual wizard for application initialization."""

from __future__ import annotations

from enum import Enum

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .initialization import (
    EventKind,
    InitializationEvent,
    InitializationPlan,
    InitializationRunner,
    InitializationStep,
    StepStatus,
)


class WizardStage(str, Enum):
    STEPS = "steps"
    EXTRAS = "extras"
    KWS_MODEL = "kws_model"
    REVIEW = "review"
    EXECUTION = "execution"


_STEP_DESCRIPTIONS = {
    InitializationStep.CONFIGURATION: "从 example 增量创建配置，不覆盖本机修改",
    InitializationStep.DEPENDENCIES: "按选择的功能执行 uv sync",
    InitializationStep.ASR_MODELS: "准备 FunASR、VAD 和标点模型",
    InitializationStep.KWS_MODEL: "下载并检查 sherpa-onnx 唤醒词模型",
    InitializationStep.VALIDATION: "解析配置并执行启动前校验",
}
_EXTRA_DESCRIPTIONS = {
    "gui": "桌面界面",
    "server": "WebSocket 服务",
    "ai": "LLM 服务",
    "data": "数据采集",
    "vision": "视觉能力",
    "hardware": "真实硬件驱动",
    "voice": "ASR 与语音输入",
    "kws": "唤醒词引擎",
    "openwakeword": "OpenWakeWord",
}
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class ChoiceList(OptionList):
    """Compact keyboard list supporting single or multiple selections."""

    class Submitted(Message):
        def __init__(self, choice_list: ChoiceList) -> None:
            self.choice_list = choice_list
            super().__init__()

    BINDINGS = [
        Binding("space", "toggle_choice", "选择", show=False),
        Binding("enter", "submit_choices", "继续", show=False),
    ]

    def __init__(
        self,
        choices: tuple[tuple[str, str, str], ...],
        *,
        selected_ids: tuple[str, ...],
        multiple: bool,
        widget_id: str,
    ) -> None:
        self._choices = {
            choice_id: (label, description)
            for choice_id, label, description in choices
        }
        self._selected_ids = set(selected_ids)
        self._multiple = multiple
        options = tuple(Option("", id=choice_id) for choice_id, _, _ in choices)
        super().__init__(*options, id=widget_id, compact=False)

    @property
    def selected_ids(self) -> tuple[str, ...]:
        return tuple(
            choice_id for choice_id in self._choices if choice_id in self._selected_ids
        )

    def on_mount(self) -> None:
        self._refresh_prompts()
        if self.option_count:
            self.highlighted = 0

    def select_all(self) -> None:
        if not self._multiple:
            return
        self._selected_ids = set(self._choices)
        self._refresh_prompts()

    def action_toggle_choice(self) -> None:
        choice_id = self._highlighted_choice_id()
        if choice_id is None:
            return
        if self._multiple:
            if choice_id in self._selected_ids:
                self._selected_ids.remove(choice_id)
            else:
                self._selected_ids.add(choice_id)
        else:
            self._selected_ids = {choice_id}
        self._refresh_prompts()

    def action_submit_choices(self) -> None:
        if not self._multiple:
            choice_id = self._highlighted_choice_id()
            if choice_id is not None:
                self._selected_ids = {choice_id}
                self._refresh_prompts()
        self.post_message(self.Submitted(self))

    def _highlighted_choice_id(self) -> str | None:
        if not self.option_count:
            return None
        return self.get_option_at_index(self.highlighted).id

    def _refresh_prompts(self) -> None:
        for choice_id in self._choices:
            self.replace_option_prompt(choice_id, self._render_choice(choice_id))

    def _render_choice(self, choice_id: str) -> Text:
        label, description = self._choices[choice_id]
        is_selected = choice_id in self._selected_ids
        marker = "●" if is_selected else "○"
        marker_style = "bold #3ddbd9" if is_selected else "#687386"
        rendered = Text()
        rendered.append(f"{marker}  ", style=marker_style)
        rendered.append(label, style="bold #e9eef7" if is_selected else "#c2cad8")
        rendered.append(f"\n   {description}", style="#778195")
        return rendered


class ReviewPrompt(Static):
    """Focusable plan summary action."""

    class Activated(Message):
        pass

    can_focus = True

    def on_click(self) -> None:
        self.post_message(self.Activated())


class StepItem(Vertical):
    """Borderless execution row with manually expandable details."""

    can_focus = True
    BINDINGS = [
        Binding("enter", "toggle_details", "详情", show=False),
        Binding("right", "expand_details", "展开", show=False),
        Binding("left", "collapse_details", "收起", show=False),
        Binding("c", "copy_details", "复制", show=False),
        Binding("up", "previous_item", "上一项", show=False),
        Binding("down", "next_item", "下一项", show=False),
    ]

    def __init__(self, step: InitializationStep) -> None:
        super().__init__(id=f"step-{step.value}", classes="step-item")
        self.step = step
        self.status = StepStatus.PENDING
        self.message = "等待执行"
        self._spinner_index = 0
        self._log_lines: list[str] = []
        self.summary = Static(classes="step-summary")
        self.detail = RichLog(highlight=True, markup=True, wrap=True, classes="step-detail")

    def compose(self) -> ComposeResult:
        yield self.summary
        yield self.detail

    def on_mount(self) -> None:
        self.detail.display = False
        self._render_summary()

    def set_status(self, status: StepStatus, message: str) -> None:
        self.status = status
        self.message = message
        self.set_classes(f"step-item {status.value}")
        self._render_summary()
        if status is StepStatus.FAILED:
            self.detail.display = True

    def write_log(self, message: str) -> None:
        self._log_lines.append(message)
        self.detail.write(f"  │ {message}")

    def tick(self) -> None:
        if self.status is not StepStatus.RUNNING:
            return
        self._spinner_index = (self._spinner_index + 1) % len(_SPINNER_FRAMES)
        self._render_summary()

    def action_toggle_details(self) -> None:
        self.detail.display = not self.detail.display

    def action_expand_details(self) -> None:
        self.detail.display = True

    def action_collapse_details(self) -> None:
        self.detail.display = False

    def action_copy_details(self) -> None:
        if not self._log_lines:
            self.app.notify("当前步骤暂无可复制的详情", severity="warning")
            return
        content = "\n".join(self._log_lines)
        self.app.copy_to_clipboard(content)
        self.app.notify(f"已复制 {self.step.label} 的完整详情")

    def action_previous_item(self) -> None:
        self.screen.focus_previous()

    def action_next_item(self) -> None:
        self.screen.focus_next()

    def on_focus(self) -> None:
        self._render_summary()

    def on_blur(self) -> None:
        self._render_summary()

    def on_click(self) -> None:
        self.focus()
        self.action_toggle_details()

    def _render_summary(self) -> None:
        pointer = "›" if self.has_focus else " "
        symbol, style = self._status_symbol()
        message = f"  {self.message}" if self.message else ""
        self.summary.update(
            f"[{style}]{pointer} {symbol}[/] [bold]{self.step.label}[/]"
            f"[#778195]{message}[/]"
        )

    def _status_symbol(self) -> tuple[str, str]:
        return {
            StepStatus.PENDING: ("○", "#687386"),
            StepStatus.RUNNING: (_SPINNER_FRAMES[self._spinner_index], "bold #3ddbd9"),
            StepStatus.SUCCEEDED: ("✓", "bold #43d17d"),
            StepStatus.FAILED: ("×", "bold #ff5c68"),
            StepStatus.SKIPPED: ("–", "#687386"),
        }[self.status]


class InitializationApp(App[int]):
    """Sequential prompt-style initializer with compact animated execution."""

    TITLE = "robot-init"
    CSS = """
    Screen {
        background: #121212;
        color: #d7dde8;
    }
    #shell {
        width: 100%;
        height: 100%;
        padding: 1 2;
    }
    #brand {
        height: 2;
        color: #3ddbd9;
        text-style: bold;
    }
    #progress-label {
        height: 1;
        color: #687386;
        margin-bottom: 1;
    }
    .page {
        height: 1fr;
    }
    .question {
        height: 2;
        color: #eef3fb;
        text-style: bold;
        margin-bottom: 1;
    }
    .help-text {
        height: 2;
        color: #687386;
        margin-top: 1;
    }
    ChoiceList {
        height: 1fr;
        background: transparent;
        border: none;
        padding: 0;
        scrollbar-size-vertical: 1;
        scrollbar-color: #394354;
        scrollbar-background: transparent;
    }
    ChoiceList > .option-list--option {
        padding: 0 1;
        background: transparent;
    }
    ChoiceList > .option-list--option-highlighted {
        background: #1c2028;
        color: #f4f7fb;
        text-style: none;
    }
    #review-summary {
        height: auto;
        padding: 0 1;
        color: #c2cad8;
    }
    ReviewPrompt {
        height: 3;
        margin-top: 1;
        padding: 1;
        color: #43d17d;
        background: transparent;
        text-style: bold;
    }
    ReviewPrompt:focus {
        background: #1c2028;
    }
    #execution-page {
        height: 1fr;
    }
    #execution-list {
        height: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-color: #394354;
        scrollbar-background: transparent;
    }
    .step-item {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        background: transparent;
    }
    .step-item:focus {
        background: #1c2028;
    }
    .step-summary {
        height: 1;
    }
    .step-detail {
        height: 7;
        margin: 1 0 0 3;
        padding: 0;
        color: #8994a7;
        background: transparent;
        scrollbar-size-vertical: 1;
    }
    """
    BINDINGS = [
        Binding("ctrl+a", "select_all", "全选", show=False),
        Binding("escape", "back_or_quit", "返回", show=False),
        Binding("ctrl+c", "cancel_or_quit", "取消", show=False, priority=True),
        Binding("q", "back_or_quit", "退出", show=False),
    ]

    def __init__(self, initial_plan: InitializationPlan) -> None:
        super().__init__()
        self._initial_plan = initial_plan
        self._stage = WizardStage.STEPS
        self._runner: InitializationRunner | None = None
        self._is_executing = False
        self._cancel_pending = False
        self._completion_exit_code = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static(
                "[#3ddbd9]robot-llm[/][#687386]:[/][bold #eef3fb]init[/] "
                "[#ff7043]$[/]",
                id="brand",
            )
            yield Static("1 / 4", id="progress-label")
            with Vertical(id="steps-page", classes="page"):
                yield Label("[#3ddbd9]?[/]  选择初始化步骤", classes="question")
                yield ChoiceList(
                    tuple(
                        (step.value, step.label, _STEP_DESCRIPTIONS[step])
                        for step in InitializationStep
                    ),
                    selected_ids=tuple(step.value for step in self._initial_plan.steps),
                    multiple=True,
                    widget_id="steps-list",
                )
                yield Static(
                    "↑↓ 移动   Space 选择   Enter 继续   Esc 退出",
                    classes="help-text",
                )
            with Vertical(id="extras-page", classes="page"):
                yield Label("[#3ddbd9]?[/]  选择需要安装的功能", classes="question")
                yield ChoiceList(
                    tuple(
                        (extra, extra, description)
                        for extra, description in _EXTRA_DESCRIPTIONS.items()
                    ),
                    selected_ids=self._initial_plan.extras,
                    multiple=True,
                    widget_id="extras-list",
                )
                yield Static(
                    "↑↓ 移动   Space 选择   Enter 继续   Esc 返回",
                    classes="help-text",
                )
            with Vertical(id="kws-page", classes="page"):
                yield Label("[#3ddbd9]?[/]  选择 KWS 唤醒词模型", classes="question")
                yield ChoiceList(
                    (
                        ("zh-en", "zh-en", "中英双语模型，推荐"),
                        ("zh", "zh", "中文兼容模型"),
                    ),
                    selected_ids=(self._initial_plan.kws_model,),
                    multiple=False,
                    widget_id="kws-list",
                )
                yield Static("↑↓ 移动   Enter 确认   Esc 返回", classes="help-text")
            with Vertical(id="review-page", classes="page"):
                yield Label("[#3ddbd9]?[/]  确认初始化计划", classes="question")
                yield Static(id="review-summary")
                yield ReviewPrompt("›  开始初始化    Enter", id="review-start")
                yield Static("Esc 返回修改   Ctrl+C 退出", classes="help-text")
            with Vertical(id="execution-page", classes="page"):
                yield Label(
                    "[#3ddbd9]◆[/]  正在初始化",
                    id="execution-title",
                    classes="question",
                )
                with VerticalScroll(id="execution-list"):
                    for step in InitializationStep:
                        yield StepItem(step)
                yield Static(
                    "↑↓ 移动   Enter/→ 展开详情   ← 收起   C 复制   Ctrl+C 取消",
                    id="execution-help",
                    classes="help-text",
                )

    def on_mount(self) -> None:
        self.set_interval(0.08, self._tick_running_steps)
        self._show_stage(WizardStage.STEPS, animate=False)

    def on_choice_list_submitted(self, event: ChoiceList.Submitted) -> None:
        if event.choice_list.id == "steps-list":
            if not event.choice_list.selected_ids:
                self.notify("至少选择一个初始化步骤", severity="warning")
                return
            self._show_stage(self._stage_after_steps())
        elif event.choice_list.id == "extras-list":
            self._show_stage(self._stage_after_extras())
        elif event.choice_list.id == "kws-list":
            self._show_stage(WizardStage.REVIEW)

    def on_review_prompt_activated(self) -> None:
        self._start_execution()

    def on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+c":
            event.stop()
            event.prevent_default()
            self.action_cancel_or_quit()
            return
        if event.key == "enter" and self._stage is WizardStage.REVIEW:
            self._start_execution()

    def action_select_all(self) -> None:
        if self._stage is WizardStage.STEPS:
            self.query_one("#steps-list", ChoiceList).select_all()
        elif self._stage is WizardStage.EXTRAS:
            self.query_one("#extras-list", ChoiceList).select_all()

    def action_back_or_quit(self) -> None:
        if self._stage is WizardStage.EXECUTION:
            if self._is_executing:
                self.notify("初始化进行中，请使用 Ctrl+C 安全取消", severity="warning")
                return
            self.exit(self._completion_exit_code)
            return
        previous = self._previous_stage()
        if previous is None:
            self.exit(0)
        else:
            self._show_stage(previous)

    def action_cancel_or_quit(self) -> None:
        if self._is_executing:
            self._cancel_pending = True
            assert self._runner is not None
            self._runner.cancel()
            self.query_one("#execution-title", Label).update(
                "[#ffcc66]◇[/]  正在取消初始化…"
            )
            self.query_one("#execution-help", Static).update(
                "正在等待当前操作安全停止"
            )
            return
        self.exit(130 if self._stage is WizardStage.EXECUTION else 0)

    def _show_stage(self, stage: WizardStage, *, animate: bool = True) -> None:
        self._stage = stage
        page_ids = {
            WizardStage.STEPS: "#steps-page",
            WizardStage.EXTRAS: "#extras-page",
            WizardStage.KWS_MODEL: "#kws-page",
            WizardStage.REVIEW: "#review-page",
            WizardStage.EXECUTION: "#execution-page",
        }
        for candidate, page_id in page_ids.items():
            self.query_one(page_id).display = candidate is stage
        self.query_one("#progress-label", Static).update(self._progress_text(stage))
        if stage is WizardStage.REVIEW:
            self._refresh_review()
        target = self.query_one(page_ids[stage])
        if animate:
            target.styles.opacity = 0.25
            target.styles.animate("opacity", value=1.0, duration=0.18)
        self._focus_stage(stage)

    def _focus_stage(self, stage: WizardStage) -> None:
        targets = {
            WizardStage.STEPS: "#steps-list",
            WizardStage.EXTRAS: "#extras-list",
            WizardStage.KWS_MODEL: "#kws-list",
            WizardStage.REVIEW: "#review-start",
        }
        selector = targets.get(stage)
        if selector is not None:
            self.query_one(selector).focus()

    def _stage_after_steps(self) -> WizardStage:
        selected = self._selected_steps()
        if InitializationStep.DEPENDENCIES in selected:
            return WizardStage.EXTRAS
        if InitializationStep.KWS_MODEL in selected:
            return WizardStage.KWS_MODEL
        return WizardStage.REVIEW

    def _stage_after_extras(self) -> WizardStage:
        if InitializationStep.KWS_MODEL in self._selected_steps():
            return WizardStage.KWS_MODEL
        return WizardStage.REVIEW

    def _previous_stage(self) -> WizardStage | None:
        if self._stage is WizardStage.STEPS:
            return None
        if self._stage is WizardStage.EXTRAS:
            return WizardStage.STEPS
        if self._stage is WizardStage.KWS_MODEL:
            return (
                WizardStage.EXTRAS
                if InitializationStep.DEPENDENCIES in self._selected_steps()
                else WizardStage.STEPS
            )
        if self._stage is WizardStage.REVIEW:
            if InitializationStep.KWS_MODEL in self._selected_steps():
                return WizardStage.KWS_MODEL
            if InitializationStep.DEPENDENCIES in self._selected_steps():
                return WizardStage.EXTRAS
            return WizardStage.STEPS
        return None

    def _progress_text(self, stage: WizardStage) -> str:
        if stage is WizardStage.EXECUTION:
            return "running"
        route = [WizardStage.STEPS]
        selected = self._selected_steps()
        if InitializationStep.DEPENDENCIES in selected:
            route.append(WizardStage.EXTRAS)
        if InitializationStep.KWS_MODEL in selected:
            route.append(WizardStage.KWS_MODEL)
        route.append(WizardStage.REVIEW)
        index = route.index(stage) + 1 if stage in route else 1
        return f"{index} / {len(route)}"

    def _selected_steps(self) -> tuple[InitializationStep, ...]:
        return tuple(
            InitializationStep(step_id)
            for step_id in self.query_one("#steps-list", ChoiceList).selected_ids
        )

    def _selected_plan(self) -> InitializationPlan:
        kws_ids = self.query_one("#kws-list", ChoiceList).selected_ids
        return InitializationPlan(
            project_root=self._initial_plan.project_root,
            steps=self._selected_steps(),
            extras=self.query_one("#extras-list", ChoiceList).selected_ids,
            kws_model=kws_ids[0] if kws_ids else "zh-en",
            frozen=self._initial_plan.frozen,
            dry_run=self._initial_plan.dry_run,
        )

    def _refresh_review(self) -> None:
        plan = self._selected_plan()
        step_lines = "\n".join(
            f"  [#43d17d]✓[/] {step.label}" for step in plan.ordered_steps
        )
        extras = ", ".join(plan.extras) if plan.extras else "无"
        mode = "演练，不产生修改" if plan.dry_run else "正式执行"
        self.query_one("#review-summary", Static).update(
            f"[#778195]模式[/]  {mode}\n\n{step_lines}\n\n"
            f"[#778195]依赖[/]  {extras}\n"
            f"[#778195]KWS[/]   {plan.kws_model}"
        )

    def _start_execution(self) -> None:
        if self._stage is not WizardStage.REVIEW or self._is_executing:
            return
        plan = self._selected_plan()
        self._is_executing = True
        self._cancel_pending = False
        self._runner = InitializationRunner(
            lambda event: self.call_from_thread(self._apply_event, event)
        )
        self._show_stage(WizardStage.EXECUTION)
        selected = frozenset(plan.ordered_steps)
        for step in InitializationStep:
            item = self.query_one(f"#step-{step.value}", StepItem)
            item.display = step in selected
            item.detail.display = False
            item.set_status(StepStatus.PENDING, "等待执行")
        self.query_one(f"#step-{plan.ordered_steps[0].value}", StepItem).focus()
        self._execute_plan(plan)

    @work(thread=True, exclusive=True, group="initialization")
    def _execute_plan(self, plan: InitializationPlan) -> None:
        assert self._runner is not None
        results = self._runner.run(plan)
        if self._runner.is_cancel_requested:
            exit_code = 130
        else:
            exit_code = 1 if any(
                result.status is StepStatus.FAILED for result in results
            ) else 0
        self.call_from_thread(self._finish_execution, exit_code)

    def _apply_event(self, event: InitializationEvent) -> None:
        item = self.query_one(f"#step-{event.step.value}", StepItem)
        if event.kind is EventKind.LOG:
            item.write_log(event.message)
            return
        assert event.status is not None
        item.set_status(event.status, event.message)

    def _finish_execution(self, exit_code: int) -> None:
        self._is_executing = False
        self._completion_exit_code = exit_code
        title = (
            "[#43d17d]◇[/]  初始化完成"
            if exit_code == 0
            else (
                "[#ffcc66]◇[/]  初始化已取消"
                if exit_code == 130
                else "[#ff5c68]◇[/]  初始化未完成"
            )
        )
        self.query_one("#execution-title", Label).update(title)
        self.query_one("#progress-label", Static).update(
            "done" if exit_code == 0 else ("cancelled" if exit_code == 130 else "failed")
        )
        if exit_code == 130:
            self.exit(130)
            return
        self.query_one("#execution-help", Static).update(
            "↑↓ 移动   Enter/→ 展开详情   ← 收起   C 复制   Esc/q 退出"
        )

    def _tick_running_steps(self) -> None:
        if self._stage is not WizardStage.EXECUTION:
            return
        for item in self.query(StepItem):
            item.tick()

    def on_unmount(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
