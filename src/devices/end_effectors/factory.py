"""Compose configured tools with borrowed communication endpoints."""

import logging
from collections.abc import Callable
from typing import Any

from ...configuration.end_effectors import EndEffectorSettings, EndEffectorTool
from ..runtime.fakes import SimulatedPipette, SimulatedToolChanger
from ..runtime.models import DeviceCapability, DeviceContractError, DeviceOperationError
from ..runtime.arm_models import ArmId
from ..runtime.runtime import DeviceRegistration, DeviceRuntime
from ..transports.core.transport import SerialSettings
from ..transports.shared_serial import SharedSerialPool
from ..tools.pipette.adapter import PipetteAdapter
from ..tools.pipette.driver import ADP
from ..tools.tool_changer.adapter import ToolChangerAdapter
from ..tools.tool_changer.driver import Kuaihuanshou
from .providers import RobotGripper, ZhixingGripper
from .runtime import EndEffectorRuntime
from ..tools.dh_rgd.driver import RGDGripper

logger = logging.getLogger(__name__)


class _ConfiguredEndpoint:
    def close(self) -> None:
        """Logical endpoint: physical communication is owned by the pool."""


class _SimulatedGripper(_ConfiguredEndpoint):
    def __init__(self) -> None:
        self.closure_percent = 0.0

    def open(self) -> None:
        self.move_closure(0)

    def grip(self) -> None:
        self.move_closure(100)

    def move_closure(self, percent: float) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("夹爪闭合比例必须为 0～100%")
        self.closure_percent = percent


class _SimulatedRGDGripper(_SimulatedGripper):
    def __init__(self) -> None:
        super().__init__()
        self.angle_degrees = 0.0

    def initialize(self) -> None:
        self.angle_degrees = 0.0

    def rotate(
        self, angle_degrees: float, *, relative: bool = False,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        if checkpoint is not None:
            checkpoint()
        self.angle_degrees = self.angle_degrees + angle_degrees if relative else angle_degrees

    def stop_rotation(self) -> None:
        """Simulation has no in-flight hardware movement."""


def register_tools(
    runtime: DeviceRuntime,
    settings: EndEffectorSettings,
    pool: SharedSerialPool,
    *,
    simulation: bool,
) -> None:
    runtime.end_effectors = EndEffectorRuntime(runtime, settings)
    for connection in settings.connections:
        _register(runtime, f"connection:{connection.id}", _ConfiguredEndpoint)
    for mount in settings.mounts:
        _register(runtime, f"end-effector:{mount.arm}", _ConfiguredEndpoint)
    selected = {
        identity
        for m in settings.mounts
        if m.mode != "none"
        for identity in ((m.tool,) if m.mode == "fixed" else (m.changer, *m.tools))
    }
    for tool in settings.tools:
        if tool.id not in selected:
            continue
        _register(
            runtime,
            f"tool:{tool.id}",
            lambda tool=tool: _create_tool(runtime, settings, pool, tool, simulation),
            {
                "gripper": DeviceCapability.GRIPPER,
                "pipette": DeviceCapability.PIPETTE,
                "tool_changer": DeviceCapability.TOOL_CHANGER,
            }[tool.kind],
            optional=True,
        )


def _register(
    runtime: DeviceRuntime,
    identity: str,
    factory: Any,
    capability: DeviceCapability | None = None,
    *,
    optional: bool = False,
) -> None:
    runtime.register(
        DeviceRegistration(
            identity,
            frozenset((capability,)) if capability else frozenset(),
            factory,
            lambda device: device.close(),
        )
    )
    # Construct a logical protocol endpoint only: no handshake or port opening.
    try:
        runtime.initialize(identity)
    except DeviceOperationError:
        if not optional:
            raise
        logger.warning("末端工具不可用，不阻止启动: %s", identity, exc_info=True)


def _create_tool(
    runtime: DeviceRuntime,
    settings: EndEffectorSettings,
    pool: SharedSerialPool,
    tool: EndEffectorTool,
    simulation: bool,
) -> Any:
    if tool.provider == "robot":
        assert runtime.end_effectors is not None
        reason = runtime.end_effectors.unavailable_reason(tool.id)
        if reason:
            raise DeviceContractError(reason)
        mount = next(m for m in settings.mounts if m.mode == "fixed" and m.tool == tool.id)
        return RobotGripper(runtime, ArmId.parse(mount.arm))
    if simulation:
        if tool.provider == "dh_rgd":
            return _SimulatedRGDGripper()
        return {
            "gripper": _SimulatedGripper,
            "pipette": SimulatedPipette,
            "tool_changer": SimulatedToolChanger,
        }[tool.kind]()
    connection = next(c for c in settings.connections if c.id == tool.connection)
    transport = pool.borrow(
        SerialSettings(
            connection.port,
            baudrate=connection.baudrate,
            bytesize=connection.bytesize,
            parity=connection.parity,
            stopbits=connection.stopbits,
            timeout_seconds=tool.response_timeout_seconds,
        )
    )
    factories = {
        "adp": lambda: PipetteAdapter(
            ADP(
                transport,
                motion_timeout_seconds=tool.motion_timeout_seconds,
                command_interval_seconds=tool.command_interval_seconds,
            )
        ),
        "kuaihuanshou": lambda: ToolChangerAdapter(Kuaihuanshou(transport)),
        "zhixing": lambda: ZhixingGripper(transport, tool),
        "dh_rgd": lambda: RGDGripper(transport, tool),
    }
    try:
        return factories[tool.provider]()
    except Exception:
        transport.close()
        raise
