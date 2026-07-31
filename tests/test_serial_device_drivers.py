from __future__ import annotations

import unittest

from src.device_control_sdk import ProtocolError
from src.device_control_sdk.testing import FakeTransport
from src.devices.adp import ADP
from src.devices.kuaihuanshou import Kuaihuanshou
from src.devices.relay import RelayController
from src.pwm_sdk import NeckController, ServoAxis


class SerialDeviceDriverTests(unittest.TestCase):
    def test_relay_uses_injected_transport_and_rejects_unknown_channel(self):
        transport = FakeTransport((b"",))
        relay = RelayController(transport)

        relay.set_channel(2, True)

        self.assertEqual(
            b"\x01\x06\x00\x01\x00\x01\x19\xCA",
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
                b"\x01\x06\x01\x07\x00\x01\xF8\x37",
            )
        )
        pipette = ADP(transport)

        self.assertTrue(pipette.initialize())
        self.assertTrue(pipette.dispense_all())
        self.assertTrue(pipette.eject_tip())

        self.assertEqual(b">01G6158", transport.calls[0].payload)
        self.assertEqual(b">01p000061AC", transport.calls[1].payload)
        self.assertEqual(
            b"\x01\x06\x01\x07\x00\x01\xF8\x37",
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


if __name__ == "__main__":
    unittest.main()
