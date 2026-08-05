from __future__ import annotations

import unittest

from src.devices.transports import ModbusRTUProtocol, ProtocolError
from src.devices.transports.testing import FakeTransport
from src.devices.tools.pipette.driver import ADP
from src.devices.tools.powder_dispenser.electric_gripper import (
    ElectricGripper,
    GripperRegister,
)
from src.devices.tools.powder_dispenser.stepper_motor import (
    MSeriesRegister,
    StepperBus,
)
from src.devices.tools.relay.driver import RelayController
from src.devices.tools.tool_changer.driver import Kuaihuanshou
from src.devices.motion.neck.pwm import NeckController, ServoAxis


class SerialDeviceDriverTests(unittest.TestCase):
    def test_powder_gripper_clamps_target_and_uses_shared_modbus_transport(self):
        protocol = ModbusRTUProtocol()
        expected_frame = protocol.build_write_register(
            9,
            GripperRegister.TARGET_POS,
            100,
        )
        transport = FakeTransport((expected_frame,))
        gripper = ElectricGripper(transport, address=9)

        gripper.move_to(150)

        self.assertEqual(expected_frame, transport.calls[0].payload)

    def test_powder_stepper_stop_preserves_address_and_register(self):
        protocol = ModbusRTUProtocol()
        expected_frame = protocol.build_write_register(
            7,
            MSeriesRegister.EMERGENCY_STOP,
            1,
        )
        transport = FakeTransport((expected_frame,))
        motor = StepperBus(transport).motor(7)

        motor.stop()

        self.assertEqual(expected_frame, transport.calls[0].payload)

    def test_relay_uses_injected_transport_and_rejects_unknown_channel(self):
        transport = FakeTransport((b"",))
        relay = RelayController(transport)

        relay.set_channel(2, True)

        self.assertEqual(
            b"\x01\x06\x00\x01\x00\x01\x19\xca",
            transport.calls[0].payload,
        )
        with self.assertRaises(ValueError):
            relay.set_channel(3, True)
        relay.close()
        self.assertTrue(transport.closed)

    def test_tool_changer_validates_response_and_decodes_status(self):
        transport = FakeTransport(
            (
                b"\x53\x26\x02\x01\x01\x00\x00",
                b"\x00",
            )
        )
        controller = Kuaihuanshou(transport)

        self.assertEqual("locked", controller.send_command("status"))
        with self.assertRaises(ProtocolError):
            controller.send_command("open")
        self.assertEqual("FixedLengthStrategy", transport.calls[0].strategy_name)

    def test_pipette_preserves_existing_ascii_frames_and_eject_protocol(self):
        transport = FakeTransport(
            (
                b"OK",
                b"OK",
                b"\x01\x06\x01\x07\x00\x01\xf8\x37",
            )
        )
        pipette = ADP(transport)

        self.assertTrue(pipette.initialize())
        self.assertTrue(pipette.dispense_all())
        self.assertTrue(pipette.eject_tip())

        self.assertEqual(b">01G6158", transport.calls[0].payload)
        self.assertEqual(b">01p000061AC", transport.calls[1].payload)
        self.assertEqual(
            b"\x01\x06\x01\x07\x00\x01\xf8\x37",
            transport.calls[2].payload,
        )

    def test_neck_writes_through_transport_and_tracks_position(self):
        transport = FakeTransport((b"",))
        neck = NeckController(transport)

        neck.move_to(1800, ServoAxis.HORIZONTAL, time_ms=0)

        self.assertEqual(b"#000P1800T0000!", transport.calls[0].payload)
        self.assertEqual(1800, neck.current_pwm[ServoAxis.HORIZONTAL])
        neck.close()
        self.assertTrue(transport.closed)

    def test_neck_rejects_out_of_range_values_instead_of_clamping(self):
        transport = FakeTransport((b"",))
        neck = NeckController(transport)

        with self.assertRaises(ValueError):
            neck.move_to(2500, ServoAxis.HORIZONTAL, time_ms=0)
        with self.assertRaises(ValueError):
            neck.move_to(1600, ServoAxis.HORIZONTAL, time_ms=10000)

        self.assertEqual([], transport.calls)


if __name__ == "__main__":
    unittest.main()
