from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...configuration.settings import (
    ApplicationSettings,
    DeviceSettings,
    RobotSettings,
    VisionSettings,
)
from ..transports import SerialSettings, SerialTransport
from ..tools.adapters import (
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
from ..robots.registry import resolve_robot_provider
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
    enter_safe_state: Callable[[Any], None] | None = None,
) -> DeviceRegistration[Any]:
    return DeviceRegistration(
        device_id=device_id,
        capabilities=frozenset(capabilities),
        factory=factory,
        close=close or (lambda device: device.close()),
        enter_safe_state=enter_safe_state,
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
            {DeviceCapability.DIGITAL_OUTPUT, DeviceCapability.SAFE_STATE},
            SimulatedDigitalOutputs,
            enter_safe_state=lambda device: device.enter_safe_state(),
        ),
        _registration(
            TOOL_CHANGER,
            {DeviceCapability.TOOL_CHANGER, DeviceCapability.SAFE_STATE},
            SimulatedToolChanger,
            enter_safe_state=lambda device: device.enter_safe_state(),
        ),
        _registration(
            PIPETTE,
            {DeviceCapability.PIPETTE, DeviceCapability.SAFE_STATE},
            SimulatedPipette,
            enter_safe_state=lambda device: device.enter_safe_state(),
        ),
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
            lambda: robot_provider.create(settings.robot),
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
            {DeviceCapability.DIGITAL_OUTPUT, DeviceCapability.SAFE_STATE},
            lambda: _relay_factory(settings.devices),
            enter_safe_state=lambda device: device.enter_safe_state(),
        )
    )
    runtime.register(
        _registration(
            TOOL_CHANGER,
            {DeviceCapability.TOOL_CHANGER, DeviceCapability.SAFE_STATE},
            lambda: _tool_changer_factory(settings.devices),
            enter_safe_state=lambda device: device.enter_safe_state(),
        )
    )
    runtime.register(
        _registration(
            PIPETTE,
            {DeviceCapability.PIPETTE, DeviceCapability.SAFE_STATE},
            lambda: _pipette_factory(settings.devices),
            enter_safe_state=lambda device: device.enter_safe_state(),
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
    from ..motion.body_axis.driver import ModbusMotor

    if ModbusMotor is None:
        raise DeviceInitializationError("ModbusMotor unavailable")
    return ModbusMotor(
        transport=SerialTransport(
            SerialSettings(
                port=settings.body_serial_port,
                baudrate=settings.body_baudrate,
                timeout_seconds=settings.body_timeout,
            )
        ),
        slave_id=settings.body_slave_id,
    )


def _mobile_base_factory(settings: RobotSettings) -> Any:
    from ..motion.mobile_base.move_controller import RobotMoveController

    controller = RobotMoveController(
        server_host=settings.move_controller_host,
        server_port=settings.move_controller_port,
        client_bind_port=settings.move_controller_client_bind_port,
    )
    controller.connect()
    return controller


def _neck_factory(settings: DeviceSettings) -> Any:
    from ..motion.neck.adapter import PWMNeckController

    if PWMNeckController is None:
        raise DeviceInitializationError("PWMNeckController unavailable")
    config = settings.pwm_neck_config()
    return PWMNeckController(
        transport=SerialTransport(
            SerialSettings(
                port=str(config["port"]),
                baudrate=int(config["baudrate"]),
                timeout_seconds=1.0,
                rts=False,
                dtr=False,
            )
        ),
        horizontal_config=config["horizontal"],
        vertical_config=config["vertical"],
    )


def _relay_factory(settings: DeviceSettings) -> RelayBankAdapter:
    from ..tools.relay.driver import RelayController

    if RelayController is None:
        raise DeviceInitializationError("RelayController unavailable")
    return RelayBankAdapter(
        RelayController(
            SerialTransport(
                SerialSettings(
                    port=settings.relay_serial_port,
                    baudrate=settings.relay_baudrate,
                    timeout_seconds=settings.relay_timeout,
                )
            )
        )
    )


def _tool_changer_factory(settings: DeviceSettings) -> ToolChangerAdapter:
    from ..tools.tool_changer.driver import Kuaihuanshou

    if Kuaihuanshou is None:
        raise DeviceInitializationError("Kuaihuanshou unavailable")
    return ToolChangerAdapter(
        Kuaihuanshou(
            SerialTransport(
                SerialSettings(
                    port=settings.kuaihuanshou_serial_port,
                    baudrate=settings.kuaihuanshou_baudrate,
                    timeout_seconds=settings.kuaihuanshou_timeout,
                )
            )
        )
    )


def _pipette_factory(settings: DeviceSettings) -> PipetteAdapter:
    from ..tools.pipette.driver import ADP
    if ADP is None:
        raise DeviceInitializationError("ADP unavailable")
    return PipetteAdapter(
        ADP(
            SerialTransport(
                SerialSettings(
                    port=settings.adp_serial_port,
                    baudrate=settings.adp_baudrate,
                    timeout_seconds=settings.adp_timeout,
                    open_attempts=settings.adp_max_retries,
                    open_retry_delay_seconds=1.0,
                )
            )
        ),
    )


def _powder_dispenser_factory(settings: DeviceSettings) -> Any:
    from ..tools.powder_dispenser.driver import TappingController

    return TappingController.from_settings(settings)


def _camera_factory(settings: VisionSettings) -> Any:
    from ..cameras.camera_factory import create_camera_manager

    manager = create_camera_manager(settings)
    if manager is None:
        raise DeviceInitializationError("camera manager unavailable")
    return manager


def _expression_display_factory(settings: DeviceSettings) -> Any:
    from ..displays.display import (
        ExpressionDisplay,
        ExpressionDisplaySettings,
    )

    return ExpressionDisplay(
        ExpressionDisplaySettings.from_mapping(
            settings.expression_display_mapping(
                Path(__file__).resolve().parents[3]
            )
        )
    )
