from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ...configuration.runtime_paths import application_root
from ..cameras.registry import resolve_camera_provider
from ..displays.display import ExpressionDisplay, ExpressionDisplaySettings
from ..displays.registry import resolve_expression_display_provider
from ..motion.mobile_base import TCP_MOBILE_BASE_PROVIDER
from ..robots.registry import resolve_robot_provider
from ...configuration.settings import (
    ApplicationSettings,
    DeviceSettings,
)
from ..transports import SerialSettings, SerialTransport
from ..transports.shared_serial import SharedSerialPool
from ..end_effectors.factory import register_tools
from ..tools.relay import RelayBankAdapter
from .fakes import (
    SimulatedBodyAxis,
    SimulatedCamera,
    SimulatedDigitalOutputs,
    SimulatedExpressionDisplay,
    SimulatedMobileBase,
    SimulatedNeck,
    SimulatedPowderDispenser,
    SimulatedRobotSystem,
)
from .ids import (
    BODY_AXIS,
    CAMERA,
    EXPRESSION_DISPLAY,
    MOBILE_BASE,
    NECK,
    POWDER_DISPENSER,
    RELAY_BANK,
    ROBOT_SYSTEM,
)
from .models import DeviceCapability, DeviceInitializationError
from .runtime import DeviceRegistration, DeviceRuntime


def create_device_runtime(
    settings: ApplicationSettings,
    *,
    simulation: bool,
) -> DeviceRuntime:
    runtime = DeviceRuntime()
    pool = SharedSerialPool(lambda options: SerialTransport(options))
    runtime.register(_registration("end-effector-connections", set(), lambda: pool))
    runtime.initialize("end-effector-connections")
    if simulation:
        _register_simulated_devices(runtime, settings)
    else:
        _register_real_devices(runtime, settings)
    register_tools(runtime, settings.end_effectors, pool, simulation=simulation)
    return runtime


def _registration(
    device_id: str,
    capabilities: set[DeviceCapability],
    factory: Callable[[], Any],
    close: Callable[[Any], None] | None = None,
    enter_safe_state: Callable[[Any], None] | None = None,
    initialize: Callable[[Any], None] | None = None,
) -> DeviceRegistration[Any]:
    return DeviceRegistration(
        device_id=device_id,
        capabilities=frozenset(capabilities),
        factory=factory,
        close=close or (lambda device: device.close()),
        enter_safe_state=enter_safe_state,
        initialize=initialize,
    )


def _register_simulated_devices(runtime: DeviceRuntime, settings: ApplicationSettings) -> None:
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
                DeviceCapability.ROBOT_TELEOPERATION,
                DeviceCapability.TRAJECTORY,
                DeviceCapability.TRAJECTORY_RECORDING,
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
        if (
            registration.device_id == ROBOT_SYSTEM
            and DeviceCapability.GRIPPER in resolve_robot_provider(settings.robot).capabilities
        ):
            registration = replace(
                registration, capabilities=registration.capabilities | {DeviceCapability.GRIPPER}
            )
        runtime.register(registration)


def _register_real_devices(
    runtime: DeviceRuntime,
    settings: ApplicationSettings,
) -> None:
    robot_provider = resolve_robot_provider(settings.robot)
    camera_provider = resolve_camera_provider(settings.vision)
    display_settings = ExpressionDisplaySettings.from_mapping(
        settings.devices.expression_display_mapping(application_root())
    )
    resolve_expression_display_provider(display_settings.provider)
    registrations = (
        _registration(
            ROBOT_SYSTEM,
            set(robot_provider.capabilities),
            lambda: robot_provider.create(settings.robot_configuration()),
        ),
        _registration(
            BODY_AXIS,
            {DeviceCapability.MOTION, DeviceCapability.BODY_AXIS},
            lambda: _body_factory(settings.devices),
        ),
        _registration(
            MOBILE_BASE,
            {DeviceCapability.MOTION, DeviceCapability.MOBILE_BASE},
            lambda: TCP_MOBILE_BASE_PROVIDER.create(settings.mobile_base),
        ),
        _registration(
            NECK,
            {DeviceCapability.MOTION, DeviceCapability.NECK_MOTION},
            lambda: _neck_factory(settings.devices),
        ),
        _registration(
            RELAY_BANK,
            {DeviceCapability.DIGITAL_OUTPUT, DeviceCapability.SAFE_STATE},
            lambda: _relay_factory(settings.devices),
            enter_safe_state=lambda device: device.enter_safe_state(),
        ),
        _registration(
            POWDER_DISPENSER,
            {DeviceCapability.MOTION, DeviceCapability.POWDER_DISPENSER},
            lambda: _powder_dispenser_factory(settings.devices),
        ),
        _registration(
            CAMERA,
            set(camera_provider.capabilities),
            lambda: camera_provider.create(settings.vision),
            lambda device: device.stop(),
        ),
        _registration(
            EXPRESSION_DISPLAY,
            {DeviceCapability.EXPRESSION_DISPLAY},
            lambda: ExpressionDisplay(display_settings),
        ),
    )
    for registration in registrations:
        runtime.register(registration)


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


def _powder_dispenser_factory(settings: DeviceSettings) -> Any:
    from ..tools.powder_dispenser.driver import TappingController

    return TappingController.from_settings(settings)
