from __future__ import annotations

import asyncio
import unittest
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import patch

from src.application import DataCollectionState
from src.core.auxiliary_services import (
    AuxiliaryServiceHost,
    AuxiliaryServiceState,
)
from src.core.launcher import (
    _shutdown_application,
    build_auxiliary_service_host,
)
from src.core.settings import ApplicationSettings
from src.robot_server.ws_server import RobotWebSocketServer


class _FakeDataCollection:
    def close(self) -> None:
        return None

    def snapshot(self):
        return SimpleNamespace(state=DataCollectionState.IDLE)


class _FakeAsyncService:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        fail_start: bool = False,
    ) -> None:
        self._name = name
        self._calls = calls
        self._fail_start = fail_start
        self.start_thread_id: int | None = None
        self.stop_thread_id: int | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def endpoint(self) -> str:
        return f"fake://{self._name}"

    async def start(self) -> None:
        self.start_thread_id = get_ident()
        self._calls.append(f"start:{self._name}")
        await asyncio.sleep(0)
        if self._fail_start:
            raise RuntimeError(f"{self._name} unavailable")

    async def stop(self) -> None:
        self.stop_thread_id = get_ident()
        self._calls.append(f"stop:{self._name}")
        await asyncio.sleep(0)


class _FakeWebSocketBinding:
    def __init__(self) -> None:
        self.closed = False
        self.waited = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.waited = True


class _FakeComposition:
    def subscribe(self, listener):
        return lambda: None


class AuxiliaryServiceHostTests(unittest.TestCase):
    def test_services_run_on_one_background_loop_and_stop_in_reverse_order(self):
        calls: list[str] = []
        first = _FakeAsyncService("first", calls)
        second = _FakeAsyncService("second", calls)
        host = AuxiliaryServiceHost(
            (first, second),
            start_timeout_seconds=0.5,
            stop_timeout_seconds=0.5,
        )

        started = host.start()

        self.assertTrue(all(snapshot.running for snapshot in started))
        self.assertIsNotNone(host.thread_id)
        self.assertNotEqual(get_ident(), host.thread_id)
        self.assertEqual(host.thread_id, first.start_thread_id)
        self.assertEqual(host.thread_id, second.start_thread_id)

        stopped = host.stop()

        self.assertTrue(
            all(
                snapshot.state is AuxiliaryServiceState.STOPPED
                for snapshot in stopped
            )
        )
        self.assertEqual(
            [
                "start:first",
                "start:second",
                "stop:second",
                "stop:first",
            ],
            calls,
        )
        self.assertEqual(host.thread_id, first.stop_thread_id)
        self.assertEqual(host.thread_id, second.stop_thread_id)

    def test_failed_optional_service_does_not_block_other_services(self):
        calls: list[str] = []
        failed = _FakeAsyncService(
            "failed",
            calls,
            fail_start=True,
        )
        running = _FakeAsyncService("running", calls)
        host = AuxiliaryServiceHost(
            (failed, running),
            start_timeout_seconds=0.5,
            stop_timeout_seconds=0.5,
        )

        with self.assertLogs(
            "src.core.auxiliary_services",
            level="WARNING",
        ):
            snapshots = {
                snapshot.name: snapshot
                for snapshot in host.start()
            }

        self.assertEqual(
            AuxiliaryServiceState.FAILED,
            snapshots["failed"].state,
        )
        self.assertIn("unavailable", snapshots["failed"].error)
        self.assertTrue(snapshots["running"].running)

        final = {
            snapshot.name: snapshot
            for snapshot in host.stop()
        }
        self.assertEqual(
            AuxiliaryServiceState.FAILED,
            final["failed"].state,
        )
        self.assertEqual(
            AuxiliaryServiceState.STOPPED,
            final["running"].state,
        )
        self.assertEqual(1, calls.count("stop:failed"))

    def test_rejects_duplicate_service_names(self):
        calls: list[str] = []
        with self.assertRaisesRegex(ValueError, "unique"):
            AuxiliaryServiceHost(
                (
                    _FakeAsyncService("same", calls),
                    _FakeAsyncService("same", calls),
                ),
                start_timeout_seconds=0.5,
                stop_timeout_seconds=0.5,
            )

    def test_disabled_host_does_not_create_a_thread(self):
        host = AuxiliaryServiceHost(
            (),
            start_timeout_seconds=0.5,
            stop_timeout_seconds=0.5,
        )

        self.assertEqual((), host.start())
        self.assertEqual((), host.stop())
        self.assertIsNone(host.thread_id)


class WebSocketServiceLifecycleTests(unittest.TestCase):
    def test_websocket_service_binds_and_closes_without_owning_application(self):
        binding = _FakeWebSocketBinding()
        captured: dict[str, object] = {}

        async def fake_serve(handler, host, port, **options):
            captured.update(
                {
                    "handler": handler,
                    "host": host,
                    "port": port,
                    "options": options,
                }
            )
            return binding

        server = RobotWebSocketServer(
            services=SimpleNamespace(
                composition=_FakeComposition(),
                data_collection=_FakeDataCollection(),
            ),
            host="127.0.0.1",
            port=9876,
        )
        server._init_ai = lambda: None
        server._init_minicpm_config = lambda: None

        async def scenario() -> None:
            with patch(
                "src.robot_server.ws_server.websockets.serve",
                new=fake_serve,
            ):
                await server.start()
                self.assertEqual("websocket", server.name)
                self.assertEqual(
                    "ws://127.0.0.1:9876/",
                    server.endpoint,
                )
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    await server.start()
                await server.stop()

        asyncio.run(scenario())

        self.assertEqual("127.0.0.1", captured["host"])
        self.assertEqual(9876, captured["port"])
        self.assertEqual(1_048_576, captured["options"]["max_size"])
        self.assertEqual(16, captured["options"]["max_queue"])
        self.assertTrue(binding.closed)
        self.assertTrue(binding.waited)


class ApplicationHostCompositionTests(unittest.TestCase):
    def test_disabled_websocket_does_not_register_a_service(self):
        args = SimpleNamespace(
            disable_websocket=False,
            websocket_host=None,
            websocket_port=None,
        )
        config = SimpleNamespace(
            WEBSOCKET_ENABLED=False,
            AUXILIARY_SERVICE_START_TIMEOUT_SECONDS=0.5,
            AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS=0.5,
        )

        host = build_auxiliary_service_host(
            args,
            ApplicationSettings.from_config(config),
            services=object(),
        )

        self.assertEqual((), host.snapshots())
        self.assertEqual((), host.start())

    def test_shutdown_stops_network_services_before_devices(self):
        calls: list[str] = []

        class FakeHost:
            def stop(self):
                calls.append("auxiliary")
                return ()

        class FakeDevices:
            def shutdown_all(self):
                calls.append("devices")
                return {}

        class FakeLocalization:
            def close(self):
                calls.append("localization")

        _shutdown_application(
            FakeHost(),
            SimpleNamespace(
                localization=FakeLocalization(),
                devices=FakeDevices(),
            ),
        )

        self.assertEqual(["auxiliary", "localization", "devices"], calls)

    def test_non_loopback_websocket_binding_uses_resolved_endpoint(self):
        args = SimpleNamespace(
            disable_websocket=False,
            websocket_host=None,
            websocket_port=None,
        )
        config = SimpleNamespace(
            WEBSOCKET_ENABLED=True,
            WEBSOCKET_HOST="0.0.0.0",
            WEBSOCKET_PORT=8765,
            WEBSOCKET_AUTH_TOKEN="test-token",
            WEBSOCKET_CONTROL_LEASE_SECONDS=30,
            AUXILIARY_SERVICE_START_TIMEOUT_SECONDS=0.5,
            AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS=0.5,
        )

        with patch(
            "src.robot_server.ws_server.RobotWebSocketServer",
            return_value=_FakeAsyncService("websocket", []),
        ) as server_type:
            host = build_auxiliary_service_host(
                args,
                ApplicationSettings.from_config(config),
                services=object(),
            )

        self.assertEqual("0.0.0.0", server_type.call_args.kwargs["host"])
        self.assertEqual(8765, server_type.call_args.kwargs["port"])
        self.assertEqual("websocket", host.snapshots()[0].name)

    def test_missing_websocket_token_warns_that_writes_are_locked(self):
        args = SimpleNamespace(
            disable_websocket=False,
            websocket_host=None,
            websocket_port=None,
        )
        config = SimpleNamespace(
            WEBSOCKET_ENABLED=True,
            WEBSOCKET_HOST="127.0.0.1",
            WEBSOCKET_PORT=8765,
            WEBSOCKET_AUTH_TOKEN="",
            WEBSOCKET_CONTROL_LEASE_SECONDS=30,
            AUXILIARY_SERVICE_START_TIMEOUT_SECONDS=0.5,
            AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS=0.5,
        )

        with (
            patch(
                "src.robot_server.ws_server.RobotWebSocketServer",
                return_value=_FakeAsyncService("websocket", []),
            ),
            self.assertLogs("src.core.launcher", level="WARNING") as logs,
        ):
            build_auxiliary_service_host(
                args,
                ApplicationSettings.from_config(config),
                services=object(),
            )

        self.assertIn(
            "所有写操作均会被拒绝",
            "\n".join(logs.output),
        )


if __name__ == "__main__":
    unittest.main()
