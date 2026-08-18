from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from src.configuration.settings import (
    ApplicationSettings,
    CameraProfile,
    CameraRole,
    DataCollectionSettings,
    DeviceSettings,
    GuiSettings,
    LLMSettings,
    LoggingSettings,
    RobotSettings,
    SecretSettings,
    ServerSettings,
    VisionSettings,
    VoiceSettings,
)


class ApplicationSettingsTests(unittest.TestCase):
    def test_visual_debug_artifacts_default_to_the_data_directory(self) -> None:
        self.assertEqual("data/vision/debug", VisionSettings().vision_debug_save_dir)

    def test_environment_values_are_split_into_domain_snapshots(self) -> None:
        settings = ApplicationSettings.from_config(
            SimpleNamespace(
                WEBSOCKET_PORT=9000,
                WEBSOCKET_SECURITY_ENABLED=True,
                ROBOT_PROVIDER="future-arm",
                RELAY_SERIAL_PORT="COM8",
                VISION_DEFAULT_CONFIDENCE=0.81,
                LLM_DEFAULT_PROVIDER="deepseek",
                DEEPSEEK_API_KEY="secret",
                VOICE_INPUT_ENABLED=True,
                DATA_COLLECTION_FPS=15,
                DATA_COLLECTION_ARMS=("right",),
                GUI_THEME="dark",
            )
        )

        self.assertIsInstance(settings.server, ServerSettings)
        self.assertIsInstance(settings.robot, RobotSettings)
        self.assertIsInstance(settings.devices, DeviceSettings)
        self.assertIsInstance(settings.vision, VisionSettings)
        self.assertIsInstance(settings.llm, LLMSettings)
        self.assertIsInstance(settings.logging, LoggingSettings)
        self.assertIsInstance(settings.gui, GuiSettings)
        self.assertIsInstance(settings.secrets, SecretSettings)
        self.assertIsInstance(settings.voice, VoiceSettings)
        self.assertIsInstance(
            settings.data_collection,
            DataCollectionSettings,
        )
        self.assertEqual(9000, settings.server.websocket_port)
        self.assertTrue(settings.server.websocket_security_enabled)
        self.assertEqual("future-arm", settings.robot.robot_provider)
        self.assertEqual("COM8", settings.devices.relay_serial_port)
        self.assertEqual(0.81, settings.vision.vision_default_confidence)
        self.assertEqual("deepseek", settings.llm.llm_default_provider)
        self.assertEqual("secret", settings.secrets.deepseek_api_key)
        self.assertTrue(settings.voice.voice_input_enabled)
        self.assertEqual(15, settings.data_collection.fps)
        self.assertEqual(("right",), settings.data_collection.arm_ids)
        self.assertEqual("dark", settings.gui.theme)

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
            )
        )

        self.assertFalse(hasattr(settings.llm, "openai_api_key"))
        self.assertEqual(
            "openai-secret",
            settings.secrets.openai_api_key,
        )
        self.assertFalse(hasattr(settings.secrets, "vveai_api_key"))

    def test_camera_catalog_resolves_stable_choices_roles_and_calibration(self) -> None:
        left = CameraProfile(
            name="monitor1",
            label="左臂视觉相机",
            provider="realsense",
            device_id="serial-left",
            roles=(
                CameraRole.VISION_CAPTURE.value,
                CameraRole.ROBOT_GRASP.value,
                CameraRole.RELOCALIZATION.value,
            ),
            arms=("left",),
            capture_rotation_matrix=(
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
            ),
            capture_translation_vector=(0.1, 0.2, 0.3),
            capture_gripper_offset=(3.14, 0.0, 1.57),
            camera_matrix=(1.0, 0.0, 2.0, 0.0, 3.0, 4.0, 0.0, 0.0, 1.0),
            camera_matrix_resolution=(1920.0, 1080.0),
            distortion_coefficients=(0.1, 0.2, 0.0, 0.0, 0.3),
            end_effector_to_camera=(
                (1.0, 0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            ),
        )
        right = CameraProfile(
            name="monitor2",
            label="右臂视觉相机",
            provider="realsense",
            device_id="serial-right",
            roles=(
                CameraRole.VISION_CAPTURE.value,
                CameraRole.ROBOT_GRASP.value,
                CameraRole.RELOCALIZATION.value,
            ),
            arms=("right",),
            capture_rotation_matrix=(
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
            ),
            capture_translation_vector=(0.0, 0.0, 0.0),
            capture_gripper_offset=(0.0, 0.0, 0.0),
            camera_matrix=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            end_effector_to_camera=(
                (1.0, 0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            ),
        )
        settings = VisionSettings(cameras=(left, right))

        self.assertEqual(
            (("monitor1", "左臂视觉相机"), ("monitor2", "右臂视觉相机")),
            settings.camera_choices(),
        )
        self.assertEqual(
            "monitor1",
            settings.camera_name_for_role(CameraRole.VISION_CAPTURE),
        )
        self.assertEqual(
            "monitor2",
            settings.camera_name_for_role(CameraRole.RELOCALIZATION, arm="right"),
        )
        self.assertEqual(
            "monitor2",
            settings.camera_name_for_role(CameraRole.ROBOT_GRASP, arm="right"),
        )
        capture = settings.capture_calibration_config(arm="left")
        self.assertEqual([0.1, 0.2, 0.3], capture["translation_vector"])
        self.assertEqual([3.14, 0.0, 1.57], capture["gripper_offset"])
        config = settings.relocalization_config("left")
        self.assertEqual("monitor1", config["camera_name"])
        self.assertEqual(
            [[1.0, 0.0, 2.0], [0.0, 3.0, 4.0], [0.0, 0.0, 1.0]],
            config["camera_matrix"],
        )
        self.assertEqual([1920.0, 1080.0], config["camera_matrix_resolution"])
        self.assertEqual([0.1, 0.2, 0.0, 0.0, 0.3], config["dist_coeffs"])

    def test_robot_grasp_camera_never_falls_back_across_arms(self) -> None:
        settings = VisionSettings(
            cameras=(
                CameraProfile(
                    name="left-grasp",
                    provider="realsense",
                    device_id="left-serial",
                    roles=(CameraRole.ROBOT_GRASP.value,),
                    arms=("left",),
                    capture_rotation_matrix=(
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                    ),
                    capture_translation_vector=(0.0, 0.0, 0.0),
                    capture_gripper_offset=(0.0, 0.0, 0.0),
                ),
            )
        )

        self.assertIsNone(
            settings.camera_for_role(CameraRole.ROBOT_GRASP, arm="right")
        )
        with self.assertRaisesRegex(ValueError, "robot_grasp.*right"):
            settings.capture_calibration_config(arm="right")

    def test_robot_grasp_role_requires_complete_calibration(self) -> None:
        with self.assertRaisesRegex(ValueError, "robot_grasp role requires"):
            CameraProfile(
                name="unsafe-grasp",
                provider="realsense",
                device_id="unsafe-serial",
                roles=(CameraRole.ROBOT_GRASP.value,),
                arms=("left",),
            )

    def test_opencv_camera_cannot_claim_depth_dependent_robot_grasp_role(self) -> None:
        with self.assertRaisesRegex(ValueError, "OpenCV.*depth frames.*robot_grasp"):
            CameraProfile(
                name="webcam",
                provider="opencv",
                device_id="0",
                roles=(CameraRole.ROBOT_GRASP.value,),
                capture_rotation_matrix=(
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                ),
                capture_translation_vector=(0.0, 0.0, 0.0),
                capture_gripper_offset=(0.0, 0.0, 0.0),
            )

    def test_relocalization_rejects_wrong_arm_and_missing_calibration(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "relocalization role requires.*camera_matrix.*end_effector_to_camera",
        ):
            CameraProfile(
                name="left-uncalibrated",
                provider="realsense",
                device_id="left-serial",
                roles=(CameraRole.RELOCALIZATION.value,),
                arms=("left",),
            )

        settings = VisionSettings(
            cameras=(
                CameraProfile(
                    name="left-calibrated",
                    provider="realsense",
                    device_id="left-serial",
                    roles=(CameraRole.RELOCALIZATION.value,),
                    arms=("left",),
                    camera_matrix=(
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                    ),
                    end_effector_to_camera=(
                        (1.0, 0.0, 0.0, 0.0),
                        (0.0, 1.0, 0.0, 0.0),
                        (0.0, 0.0, 1.0, 0.0),
                        (0.0, 0.0, 0.0, 1.0),
                    ),
                ),
            )
        )

        with self.assertRaisesRegex(ValueError, "relocalization.*right"):
            settings.relocalization_config(
                "right",
                camera_name="left-calibrated",
            )

    def test_camera_catalog_rejects_duplicate_identity_and_mixed_providers(self) -> None:
        profile = CameraProfile(
            name="monitor1",
            provider="realsense",
            device_id="serial-1",
        )
        with self.assertRaisesRegex(ValueError, "duplicate camera profile name"):
            VisionSettings(cameras=(profile, profile))
        with self.assertRaisesRegex(ValueError, "requires one provider"):
            VisionSettings(
                cameras=(
                    profile,
                    CameraProfile(
                        name="webcam",
                        provider="opencv",
                        device_id="0",
                    ),
                )
            )


if __name__ == "__main__":
    unittest.main()
