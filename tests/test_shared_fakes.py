from __future__ import annotations

import unittest

from src.devices.transports.core.exceptions import TransportError
from src.devices.transports.testing import FakeTransport
from src.llm.testing import FakeLLMClient
from src.llm.types import LLMMessage


class FakeTransportTests(unittest.TestCase):
    def test_scripted_response_records_request_and_enforces_lifecycle(self):
        transport = FakeTransport((b"\x01\x02",))

        self.assertEqual(b"\x01\x02", transport.transact(b"\x10", 2))
        self.assertEqual(b"\x10", transport.calls[0].payload)
        transport.close()

        with self.assertRaises(TransportError):
            transport.transact(b"\x20", 1)


class FakeLLMClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_is_deterministic_and_records_inputs(self):
        client = FakeLLMClient("offline", chat_text="answer")
        messages = [LLMMessage(role="user", content="question")]

        result = await client.chat(messages, temperature=0)

        self.assertEqual("answer", result.text)
        self.assertEqual(1, len(client.chat_calls))
        self.assertEqual(0, client.chat_calls[0][1]["temperature"])
        await client.close()
        self.assertFalse(client.is_available())


if __name__ == "__main__":
    unittest.main()
