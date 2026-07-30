from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.settings import (
    ApplicationSettings,
    DeviceSettings,
    RobotSettings,
    VisionSettings,
)
from .adapters import (
    PipetteAdapter,
    RelayBankAdapter,
    ToolChangerAdapter,
)
from .fakes import (
    SimulatedBodyAxis,
    SimulatedCamera,
    SimulatedDigitalOutputs,
    SimulatedExpressionDisplay,
    SimulatedMobileBase,
    SimulatedNeck,
    SimulatedPipette,
    SimulatedPowderDispenser,
    SimulatedRobotSystem,
    SimulatedToolChanger,
)
from .ids import (
    BODY_AXIS,
    CAMERA,
    EXPRESSION_DISPLAY,
    MOBILE_BASE,
    NECK,
    PIPETTE,
    POWDER_DISPENSER,
    RELAY_BANK,
    ROBOT_SYSTEM,
    TOOL_CHANGER,
)
from .models import DeviceCapability, DeviceInitializationError
from .robot_providers import resolve_robot_provider
from .runtime import DeviceRegistration, DeviceRuntime


def create_device_runtime(
    settings: ApplicationSettings,
    *,
    simulation: bool,
) -> DeviceRuntime:
    runtime = DeviceRuntime()
    if simulation:
        _register_simulated_devices(runtime)
    else:
        _register_real_devices(runtime, settings)
    return runtime


def _registration(
    device_id: str,
    capabilities: set[DeviceCapability],
    factory: Callable[[], Any],
    close: Callable[[Any], None] | None = None,
) -> DeviceRegistration[Any]:
    return DeviceRegistration(
        device_id=device_id,
        capabilities=frozenset(capabilities),
        factory=factory,
        close=close or (lambda device: device.close()),
    )


def _register_simulated_devices(runtime: DeviceRuntime) -> None:
    registrations = (
        _registration(
            ROBOT_SYSTEM,
            {
                DeviceCapability.MOTION,
                DeviceCapability.QUICK_STOP,
                DeviceCapability.EMERGENCY_STOP,
                DeviceCapability.ARM_MOTION,
                DeviceCapability.ARM_STATE,
                DeviceCapability.ARM_TELEMETRY,
                DeviceCapability.GRIPPER,
                DeviceCapability.ROBOT_TELEOPERATION,
                DeviceCapability.TRAJECTORY,
                DeviceCapability.TOOL_RACK,
            },
            SimulatedRobotSystem,
        ),
        _registration(
            BODY_AXIS,
            {DeviceCapability.MOTION, DeviceCapability.BODY_AXIS},
            SimulatedBodyAxis,
        ),
        _registration(
            MOBILE_BASE,
            {DeviceCapability.MOTION, DeviceCapability.MOBILE_BASE},
            SimulatedMobileBase,
        ),
        _registration(
            NECK,
            {DeviceCapability.MOTION, DeviceCapability.NECK_MOTION},
            SimulatedNeck,
        ),
        _registration(
            RELAY_BANK,
            {DeviceCapability.DIGITAL_OUTPUT},
            SimulatedDigitalOutputs,
        ),
        _registration(
            TOOL_CHANGER,
            {DeviceCapability.TOOL_CHANGER},
            SimulatedToolChanger,
        ),
        _registration(PIPETTE, {DeviceCapability.PIPETTE}, SimulatedPipette),
        _registration(
            POWDER_DISPENSER,
            {
                DeviceCapability.MOTION,
                DeviceCapability.POWDER_DISPENSER,
            },
            SimulatedPowderDispenser,
        ),
        _registration(
            CAMERA,
            {DeviceCapability.CAMERA},
            SimulatedCamera,
            lambda device: device.stop(),
        ),
        _registration(
            EXPRESSION_DISPLAY,
            {DeviceCapability.EXPRESSION_DISPLAY},
            SimulatedExpressionDisplay,
        ),
    )
    for registration in registrations:
        runtime.register(registration)


def _register_real_devices(
    runtime: DeviceRuntime,
    settings: ApplicationSettings,
) -> None:
    robot_provider = resolve_robot_provider(settings.robot)
    runtime.register(
        _registration(
            ROBOT_SYSTEM,
            set(robot_provider.capabilities),
            lambda: robot_provider.create(settings.robot, settings.devices),
        )
    )
    runtime.register(
        _registration(
            BODY_AXIS,
            {DeviceCapability.MOTION, DeviceCapability.BODY_AXIS},
            lambda: _body_factory(settings.devices),
        )
    )
    runtime.register(
        _registration(
            MOBILE_BASE,
            {DeviceCapability.MOTION, DeviceCapability.MOBILE_BASE},
            lambda: _mobile_base_factory(settings.robot),
        )
    )
    runtime.register(
        _registration(
            NECK,
            {DeviceCapability.MOTION, DeviceCapability.NECK_MOTION},
            lambda: _neck_factory(settings.devices),
        )
    )
    runtime.register(
        _registration(
            RELAY_BANK,
            {DeviceCapability.DIGITAL_OUTPUT},
            lambda: _relay_factory(settings.devices),
        )
    )
    runtime.register(
        _registration(
            TOOL_CHANGER,
            {DeviceCapability.TOOL_CHANGER},
            lambda: _tool_changer_factory(settings.devices),
        )
    )
    runtime.register(
        _registration(
            PIPETTE,
            {DeviceCapability.PIPETTE},
            lambda: _pipette_factory(settings.devices),
        )
    )
    runtime.register(
        _registration(
            POWDER_DISPENSER,
            {
                DeviceCapability.MOTION,
                DeviceCapability.POWDER_DISPENSER,
            },
            lambda: _powder_dispenser_factory(settings.devices),
        )
    )
    runtime.register(
        _registration(
            CAMERA,
            {DeviceCapability.CAMERA},
            lambda: _camera_factory(settings.vision),
            lambda device: device.stop(),
        )
    )
    runtime.register(
        _registration(
            EXPRESSION_DISPLAY,
            {DeviceCapability.EXPRESSION_DISPLAY},
            lambda: _expression_display_factory(settings.devices),
        )
    )


def _body_factory(settings: DeviceSettings) -> Any:
    from ..devices import ModbusMotor

    if ModbusMotor is None:
        raise DeviceInitializationError("ModbusMotor unavailable")
    return ModbusMotor(
        port=settings.body_serial_port,
        baudrate=settings.body_baudrate,
        slave_id=settings.body_slave_id,
        timeout=settings.body_timeout,
    )


def _mobile_base_factory(settings: RobotSettings) -> Any:
    from ..base_move.move_controller import RobotMoveController

    controller = RobotMoveController(
        server_host=settings.move_controller_host,
        server_port=settings.move_controller_port,
        client_bind_port=settings.move_controller_client_bind_port,
    )
    controller.connect()
    return controller


def _neck_factory(settings: DeviceSettings) -> Any:
    from ..devices import PWMNeckController

    if PWMNeckController is None:
        raise DeviceInitializationError("PWMNeckController unavailable")
    config = settings.pwm_neck_config()
    return PWMNeckController(
        port=config["port"],
        baudrate=config["baudrate"],
        horizontal_config=config["horizontal"],
        vertical_config=config["vertical"],
    )


def _relay_factory(settings: DeviceSettings) -> RelayBankAdapter:
    from ..devices import RelayController

    if RelayController is None:
        raise DeviceInitializationError("RelayController unavailable")
    return RelayBankAdapter(
        RelayController(
            port=settings.relay_serial_port,
            baudrate=settings.relay_baudrate,
            timeout=settings.relay_timeout,
        )
    )


def _tool_changer_factory(settings: DeviceSettings) -> ToolChangerAdapter:
    from ..devices import Kuaihuanshou

    if Kuaihuanshou is None:
        raise DeviceInitializationError("Kuaihuanshou unavailable")
    return ToolChangerAdapter(
        Kuaihuanshou(
            port=settings.kuaihuanshou_serial_port,
            baudrate=settings.kuaihuanshou_baudrate,
            timeout=settings.kuaihuanshou_timeout,
        )
    )


def _pipette_factory(settings: DeviceSettings) -> PipetteAdapter:
    from ..devices import ADP
    from ..devices.yiyeqiang_init import init_tip
    from ..devices.yiyeqiang_out import eject_tip

    if ADP is None:
        raise DeviceInitializationError("ADP unavailable")
    return PipetteAdapter(
        ADP(
            port=settings.adp_serial_port,
            baudrate=settings.adp_baudrate,
            timeout=settings.adp_timeout,
            max_retries=settings.adp_max_retries,
        ),
        tip_port=settings.kuaihuanshou_serial_port,
        initialize_tip=init_tip,
        eject_tip=eject_tip,
    )


def _powder_dispenser_factory(settings: DeviceSettings) -> Any:
    from ..devices.tapping_controller import TappingController

    return TappingController.from_settings(settings)


def _camera_factory(settings: VisionSettings) -> Any:
    from ..cameras.camera_factory import create_camera_manager

    manager = create_camera_manager(settings)
    if manager is None:
        raise DeviceInitializationError("camera manager unavailable")
    return manager


def _expression_display_factory(settings: DeviceSettings) -> Any:
    from ..expression_display.display import (
        ExpressionDisplay,
        ExpressionDisplaySettings,
    )

    return ExpressionDisplay(
        ExpressionDisplaySettings.from_mapping(
            settings.expression_display_mapping(
                Path(__file__).resolve().parents[2]
            )
        )
    )
