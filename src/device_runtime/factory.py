from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .adapters import (
    PipetteAdapter,
    RealManGripperOptions,
    RealManRobotAdapter,
    RelayBankAdapter,
    ToolChangerAdapter,
)
from .arm_models import ArmId, MotionOptions
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
from .runtime import DeviceRegistration, DeviceRuntime


def create_device_runtime(config: Any, *, simulation: bool) -> DeviceRuntime:
    runtime = DeviceRuntime()
    if simulation:
        _register_simulated_devices(runtime)
    else:
        _register_real_devices(runtime, config)
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
                DeviceCapability.ARM_MOTION,
                DeviceCapability.ARM_STATE,
                DeviceCapability.GRIPPER,
                DeviceCapability.ROBOT_TELEOPERATION,
                DeviceCapability.TRAJECTORY,
                DeviceCapability.TOOL_RACK,
            },
            SimulatedRobotSystem,
        ),
        _registration(
            BODY_AXIS,
            {DeviceCapability.BODY_AXIS},
            SimulatedBodyAxis,
        ),
        _registration(
            MOBILE_BASE,
            {DeviceCapability.MOBILE_BASE},
            SimulatedMobileBase,
        ),
        _registration(NECK, {DeviceCapability.NECK_MOTION}, SimulatedNeck),
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
            {DeviceCapability.POWDER_DISPENSER},
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


def _register_real_devices(runtime: DeviceRuntime, config: Any) -> None:
    runtime.register(
        _registration(
            ROBOT_SYSTEM,
            {
                DeviceCapability.ARM_MOTION,
                DeviceCapability.ARM_STATE,
                DeviceCapability.GRIPPER,
                DeviceCapability.ROBOT_TELEOPERATION,
                DeviceCapability.TRAJECTORY,
                DeviceCapability.TOOL_RACK,
            },
            lambda: _robot_factory(config),
        )
    )
    runtime.register(
        _registration(
            BODY_AXIS,
            {DeviceCapability.BODY_AXIS},
            lambda: _body_factory(config),
        )
    )
    runtime.register(
        _registration(
            MOBILE_BASE,
            {DeviceCapability.MOBILE_BASE},
            _mobile_base_factory,
        )
    )
    runtime.register(
        _registration(
            NECK,
            {DeviceCapability.NECK_MOTION},
            _neck_factory,
        )
    )
    runtime.register(
        _registration(
            RELAY_BANK,
            {DeviceCapability.DIGITAL_OUTPUT},
            lambda: _relay_factory(config),
        )
    )
    runtime.register(
        _registration(
            TOOL_CHANGER,
            {DeviceCapability.TOOL_CHANGER},
            lambda: _tool_changer_factory(config),
        )
    )
    runtime.register(
        _registration(
            PIPETTE,
            {DeviceCapability.PIPETTE},
            lambda: _pipette_factory(config),
        )
    )
    runtime.register(
        _registration(
            POWDER_DISPENSER,
            {DeviceCapability.POWDER_DISPENSER},
            _powder_dispenser_factory,
        )
    )
    runtime.register(
        _registration(
            CAMERA,
            {DeviceCapability.CAMERA},
            _camera_factory,
            lambda device: device.stop(),
        )
    )
    runtime.register(
        _registration(
            EXPRESSION_DISPLAY,
            {DeviceCapability.EXPRESSION_DISPLAY},
            _expression_display_factory,
        )
    )


def _robot_factory(config: Any) -> RealManRobotAdapter:
    provider = str(getattr(config, "ROBOT_PROVIDER", "realman")).strip().lower()
    if provider != "realman":
        raise DeviceInitializationError(
            f"unsupported robot provider: {provider}"
        )

    from ..arm_sdk import RobotController
    from ..devices import yiyeqiang_out

    if RobotController is None:
        raise DeviceInitializationError("RobotController SDK unavailable")
    move_options = MotionOptions(
        velocity_percent=int(getattr(config, "MOVE_VELOCITY", 10)),
        blend_radius=int(getattr(config, "MOVE_RADIUS", 0)),
        connected=bool(getattr(config, "MOVE_CONNECT", 0)),
        blocking=bool(getattr(config, "MOVE_BLOCK", 1)),
    )
    gripper_options = RealManGripperOptions(
        pick_speed=int(getattr(config, "GRIPPER_PICK_SPEED", 200)),
        pick_force=int(getattr(config, "GRIPPER_PICK_FORCE", 1000)),
        pick_timeout_s=int(getattr(config, "GRIPPER_PICK_TIMEOUT", 3)),
        release_speed=int(
            getattr(config, "GRIPPER_RELEASE_SPEED", 100)
        ),
        release_timeout_s=int(
            getattr(config, "GRIPPER_RELEASE_TIMEOUT", 3)
        ),
        max_attempts=int(getattr(config, "MAX_ATTEMPTS", 5)),
    )
    controller = RobotController()
    adapter = RealManRobotAdapter(
        controller,
        default_motion=move_options,
        gripper_options=gripper_options,
        eject_tool=lambda: yiyeqiang_out.eject_tip(
            port=str(getattr(config, "KUAIHUANSHOU_SERIAL_PORT", "COM4"))
        ),
    )
    try:
        adapter.read_arm_state(ArmId.LEFT)
        adapter.read_arm_state(ArmId.RIGHT)
        return adapter
    except Exception:
        adapter.close()
        raise


def _body_factory(config: Any) -> Any:
    from ..devices import ModbusMotor

    if ModbusMotor is None:
        raise DeviceInitializationError("ModbusMotor unavailable")
    return ModbusMotor(
        port=config.BODY_SERIAL_PORT,
        baudrate=115200,
        slave_id=1,
        timeout=1,
    )


def _mobile_base_factory() -> Any:
    from ..base_move.move_controller import RobotMoveController

    controller = RobotMoveController()
    controller.connect()
    return controller


def _neck_factory() -> Any:
    from ..devices import PWMNeckController

    if PWMNeckController is None:
        raise DeviceInitializationError("PWMNeckController unavailable")
    return PWMNeckController()


def _relay_factory(config: Any) -> RelayBankAdapter:
    from ..devices import RelayController

    if RelayController is None:
        raise DeviceInitializationError("RelayController unavailable")
    return RelayBankAdapter(
        RelayController(
            port=config.RELAY_SERIAL_PORT,
            baudrate=config.RELAY_BAUDRATE,
            timeout=config.RELAY_TIMEOUT,
        )
    )


def _tool_changer_factory(config: Any) -> ToolChangerAdapter:
    from ..devices import Kuaihuanshou

    if Kuaihuanshou is None:
        raise DeviceInitializationError("Kuaihuanshou unavailable")
    return ToolChangerAdapter(
        Kuaihuanshou(port=config.KUAIHUANSHOU_SERIAL_PORT)
    )


def _pipette_factory(config: Any) -> PipetteAdapter:
    from ..devices import ADP
    from ..devices.yiyeqiang_init import init_tip
    from ..devices.yiyeqiang_out import eject_tip

    if ADP is None:
        raise DeviceInitializationError("ADP unavailable")
    adp_port = getattr(config, "ADP_SERIAL_PORT", config.KUAIHUANSHOU_SERIAL_PORT)
    return PipetteAdapter(
        ADP(port=adp_port),
        tip_port=config.KUAIHUANSHOU_SERIAL_PORT,
        initialize_tip=init_tip,
        eject_tip=eject_tip,
    )


def _powder_dispenser_factory() -> Any:
    from ..devices.tapping_controller import TappingController

    return TappingController.from_config()


def _camera_factory() -> Any:
    from ..cameras.camera_factory import create_camera_manager

    manager = create_camera_manager()
    if manager is None:
        raise DeviceInitializationError("camera manager unavailable")
    return manager


def _expression_display_factory() -> Any:
    from ..expression_display.display import (
        ExpressionDisplay,
        ExpressionDisplaySettings,
    )

    return ExpressionDisplay(ExpressionDisplaySettings.from_project_config())
