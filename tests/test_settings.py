from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from src.core.settings import (
    ApplicationSettings,
    DataCollectionSettings,
    DeviceSettings,
    LLMSettings,
    LoggingSettings,
    RobotSettings,
    SecretSettings,
    ServerSettings,
    VisionSettings,
    VoiceSettings,
)


class ApplicationSettingsTests(unittest.TestCase):
    def test_environment_values_are_split_into_domain_snapshots(self) -> None:
        settings = ApplicationSettings.from_config(
            SimpleNamespace(
                WEBSOCKET_PORT=9000,
                ROBOT_PROVIDER="future-arm",
                RELAY_SERIAL_PORT="COM8",
                VISION_DEFAULT_CONFIDENCE=0.81,
                LLM_DEFAULT_PROVIDER="deepseek",
                DEEPSEEK_API_KEY="secret",
                VOICE_INPUT_ENABLED=True,
                DATA_COLLECTION_FPS=15,
                DATA_COLLECTION_ARMS=("right",),
            )
        )

        self.assertIsInstance(settings.server, ServerSettings)
        self.assertIsInstance(settings.robot, RobotSettings)
        self.assertIsInstance(settings.devices, DeviceSettings)
        self.assertIsInstance(settings.vision, VisionSettings)
        self.assertIsInstance(settings.llm, LLMSettings)
        self.assertIsInstance(settings.logging, LoggingSettings)
        self.assertIsInstance(settings.secrets, SecretSettings)
        self.assertIsInstance(settings.voice, VoiceSettings)
        self.assertIsInstance(
            settings.data_collection,
            DataCollectionSettings,
        )
        self.assertEqual(9000, settings.server.websocket_port)
        self.assertEqual("future-arm", settings.robot.robot_provider)
        self.assertEqual("COM8", settings.devices.relay_serial_port)
        self.assertEqual(0.81, settings.vision.vision_default_confidence)
        self.assertEqual("deepseek", settings.llm.llm_default_provider)
        self.assertEqual("secret", settings.secrets.deepseek_api_key)
        self.assertTrue(settings.voice.voice_input_enabled)
        self.assertEqual(15, settings.data_collection.fps)
        self.assertEqual(("right",), settings.data_collection.arm_ids)

    def test_logging_settings_use_explicit_environment_names(self) -> None:
        settings = ApplicationSettings.from_config(
            SimpleNamespace(
                LOG_LEVEL="DEBUG",
                LOG_DIRECTORY="runtime-logs",
                LOG_RETENTION_DAYS=30,
            )
        )

        self.assertEqual("DEBUG", settings.logging.level)
        self.assertEqual("runtime-logs", settings.logging.directory)
        self.assertEqual(30, settings.logging.retention_days)

    def test_snapshots_and_nested_sequences_are_immutable(self) -> None:
        source_pose = [1, 2, 3, 4, 5, 6]
        settings = ApplicationSettings.from_config(SimpleNamespace(ROBOT1_INITIAL_POSE=source_pose))
        source_pose[0] = 99

        self.assertEqual(
            (1, 2, 3, 4, 5, 6),
            settings.robot.robot1_initial_pose,
        )
        with self.assertRaises(FrozenInstanceError):
            settings.server.websocket_port = 9001

    def test_secrets_do_not_leak_into_provider_settings(self) -> None:
        settings = ApplicationSettings.from_config(
            SimpleNamespace(
                OPENAI_API_KEY="openai-secret",
                DEEPSEEK_API_KEY="deepseek-secret",
                DASHSCOPE_API_KEY="dashscope-secret",
                VVEAI_API_KEY="vision-secret",
            )
        )

        self.assertFalse(hasattr(settings.llm, "openai_api_key"))
        self.assertEqual(
            "openai-secret",
            settings.secrets.openai_api_key,
        )
        self.assertEqual(
            "vision-secret",
            settings.secrets.vveai_api_key,
        )


if __name__ == "__main__":
    unittest.main()
