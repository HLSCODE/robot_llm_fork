from __future__ import annotations

from threading import RLock

from ..base import ExpressionSpec
from ..provider import ExpressionDisplayProviderDefinition


class T5LDgusiiExpressionDisplay:
    """T5L DGUSII expression display strategy.

    This class owns T5L-specific SDK container reuse. The project-level
    facade only chooses this strategy; it does not manage serial internals.
    """

    def __init__(self, settings):
        self.settings = settings
        self._lock = RLock()
        self._container = None
        self._sdk_config = None

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def _ensure_enabled(self) -> None:
        if not self.settings.enabled:
            from ..display import ExpressionDisplayUnavailable

            raise ExpressionDisplayUnavailable(
                "Expression display is disabled. Set EXPRESSION_DISPLAY_ENABLED=true to use it."
            )

    def _raise_optional_dependency_error(self, exc: ModuleNotFoundError) -> None:
        if exc.name == "serial":
            from ..display import ExpressionDisplayUnavailable

            raise ExpressionDisplayUnavailable(
                "T5L DGUSII expression display requires pyserial. "
                "Install project dependencies before using it."
            ) from exc
        raise exc

    def _get_container(self):
        try:
            from . import T5LServiceContainer
        except ModuleNotFoundError as exc:
            self._raise_optional_dependency_error(exc)

        if self._container is None:
            self._container = T5LServiceContainer()
        return self._container

    def _get_sdk_config(self):
        if self._sdk_config is None:
            self._sdk_config = self._build_sdk_config()
        return self._sdk_config

    def _build_sdk_config(self):
        try:
            from .config import DgusSdkConfig, ExpressionConfig
            from .controls import AnimationIconConfig
            from .transport import SerialConfig
        except ModuleNotFoundError as exc:
            self._raise_optional_dependency_error(exc)

        return DgusSdkConfig(
            serial=SerialConfig(
                port=self.settings.port,
                baudrate=self.settings.baudrate,
                timeout=self.settings.timeout,
                write_timeout=self.settings.write_timeout,
            ),
            animation_icon=AnimationIconConfig(
                vp_addr=self.settings.vp_addr,
                sp_addr=self.settings.sp_addr,
                start_value=self.settings.start_value,
                stop_value=self.settings.stop_value,
                hide_value=self.settings.hide_value,
                clear_before_switch=self.settings.clear_before_switch,
                switch_delay=self.settings.switch_delay,
                update_icon_range=self.settings.update_icon_range,
            ),
            expressions=[
                ExpressionConfig(
                    name=item.name,
                    icon_lib=item.icon_lib,
                    icon_start=item.icon_start,
                    icon_end=item.icon_end,
                )
                for item in self.settings.expressions
            ],
            clear_vps=list(self.settings.clear_vps),
            test_interval=self.settings.test_interval,
        )

    def _get_service(self):
        self._ensure_enabled()
        container = self._get_container()
        if self.settings.config_path is not None:
            return container.get_expression_service(
                self.settings.config_path,
                tx_delay=self.settings.tx_delay,
            )
        return container.get_expression_service(
            config_path=None,
            config=self._get_sdk_config(),
            tx_delay=self.settings.tx_delay,
        )

    def list_configured_expressions(self) -> list[ExpressionSpec]:
        if self.settings.config_path is None:
            return list(self.settings.expressions)

        try:
            from . import load_config
        except ModuleNotFoundError as exc:
            self._raise_optional_dependency_error(exc)

        config = load_config(self.settings.config_path)
        return [
            ExpressionSpec(item.name, item.icon_lib, item.icon_start, item.icon_end)
            for item in config.expressions
        ]

    def switch(self, expression: str | int):
        with self._lock:
            return self._get_service().switch(expression)

    def run_test(self) -> None:
        with self._lock:
            self._get_service().run_test()

    def close(self) -> None:
        with self._lock:
            if self._container is not None:
                self._container.close()
                self._container = None
            self._sdk_config = None


def create_display(settings) -> T5LDgusiiExpressionDisplay:
    return T5LDgusiiExpressionDisplay(settings)


T5L_DGUSII_DISPLAY_PROVIDER = ExpressionDisplayProviderDefinition(
    name="t5l_dgusii",
    create=create_display,
)

__all__ = [
    "T5L_DGUSII_DISPLAY_PROVIDER",
    "T5LDgusiiExpressionDisplay",
    "create_display",
]
