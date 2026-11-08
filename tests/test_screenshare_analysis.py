import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_screenshare import analyze, read_logs


def line(thread, message, source="video_stream_encoder.cc"):
    return f"[2026-11-01 12:00:00.000] [{thread}] ({source}:123): {message}\n"


class ScreenshareAnalysis(unittest.TestCase):
    def test_interleaved_camera_and_share_are_not_combined(self):
        report = analyze(
            [
                line(
                    10, "[VSE] ReconfigureEncoder [encoder_config={content_type: kRealtimeVideo}]"
                ),
                line(20, "[VSE] ReconfigureEncoder [encoder_config={content_type: kScreenshare}]"),
                line(
                    10,
                    "INFO [vaapi_media_encoding::vulkan_backend] Creating CPU surface pool for 1280x720",
                    "vulkan_backend.rs",
                ),
                line(20, "Drop Frame: target bitrate 1456768, input frame rate 60"),
                line(20, "Drop Frame: target bitrate 500000, input frame rate 60"),
                line(
                    20,
                    "Number of frames: captured 3601, dropped (due to congestion window pushback) 0, dropped (due to encoder blocked) 4, interval_ms 60000",
                ),
                line(
                    20,
                    'Resource "QualityScalerResource" signalled kOveruse. Adapted down successfully. Unfiltered adaptations: { res=1 fps=0 }',
                    "resource_adaptation_processor.cc",
                ),
                line(
                    20,
                    'Resource "EncoderUsageResource" signalled kOveruse. Not adapting down because VideoStreamAdapter returned kLimitReached',
                    "resource_adaptation_processor.cc",
                ),
            ]
        )
        camera, share = report["observations"]
        self.assertEqual(camera["content_types"], ["kRealtimeVideo"])
        self.assertNotIn("cpu_surface_pool_creations", share["events"])
        self.assertEqual(share["events"]["bitrate_frame_drops"], 2)
        self.assertEqual(share["events"]["interval_encoder_blocked"], 4)
        self.assertEqual(share["interval_capture_fps"]["last"], 60.017)
        self.assertEqual(share["drop_target_bps"]["min"], 500000)
        self.assertEqual(share["events"]["QualityScalerResource_down"], 1)
        self.assertNotIn("EncoderUsageResource_down", share["events"])

    def test_cpu_adaptation_counts_only_successful_changes(self):
        report = analyze(
            [line(20, "content_type: kScreenshare")]
            + [
                line(
                    20,
                    f'Resource "EncoderUsageResource" signalled k{usage}. {outcome}',
                    "resource_adaptation_processor.cc",
                )
                for usage, outcome in [
                    (
                        "Overuse",
                        "Adapted down successfully. Unfiltered adaptations: { res=1 fps=0 }",
                    ),
                    (
                        "Underuse",
                        "Adapted up successfully. Unfiltered adaptations: { res=0 fps=0 }",
                    ),
                    (
                        "Overuse",
                        "Not adapting down because VideoStreamAdapter returned kLimitReached",
                    ),
                ]
            ]
        )
        self.assertEqual(
            report["observations"][0]["events"],
            {"EncoderUsageResource_down": 1, "EncoderUsageResource_up": 1},
        )

    def test_report_never_includes_native_strings_or_identifiers(self):
        secret = "private-user-address-key-path"
        report = analyze(
            [
                line(987654321, secret),
                line(
                    987654321,
                    f"Encoder info changed to EncoderInfo {{ implementation_name = '{secret}', is_hardware_accelerated = 1, supports_native_handle = 1, has_trusted_rate_controller = 0 }}",
                ),
                line(
                    987654321,
                    f"[VSE] ReconfigureEncoder [encoder_config={{content_type: kScreenshare, target_bitrate_bps: 150000, secret: {secret}}}]",
                ),
                line(12, f"Drop Frame: target bitrate 1, input frame rate 60 {secret}"),
                line(13, "Drop Frame: target bitrate 1, input frame rate 60", "unrelated.cc"),
            ]
        )
        serialized = json.dumps(report)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("987654321", serialized)
        self.assertEqual(len(report["observations"]), 1)
        group = report["observations"][0]
        self.assertTrue(group["is_hardware_accelerated"])
        self.assertFalse(group["has_trusted_rate_controller"])
        self.assertEqual(group["configured_target_bitrate_bps"]["last"], 150000)

    def test_requested_and_effective_bitrate_limits_remain_distinct(self):
        report = analyze(
            [
                line(1, "[VSE] ReconfigureEncoder [encoder_config={max_bitrate_bps:9000000}]"),
                line(
                    1,
                    "[VSE] ReconfigureEncoder: simulcast streams: "
                    "{0: 1920x1080 L1T1, min_kbps: 30, target_kbps: 4000, max_kbps: 4000}, "
                    "{1: 640x360, min_kbps: 20, target_kbps: 500, max_kbps: 1000}, "
                    "spatial layers: {0: max_kbps: 99999}",
                ),
            ]
        )
        group = report["observations"][0]
        self.assertEqual(group["configured_max_bitrate_bps"]["last"], 9000000)
        self.assertEqual(group["effective_max_bitrate_bps"]["max"], 4000000)
        self.assertEqual(group["effective_max_bitrate_bps"]["last"], 1000000)
        self.assertEqual(group["effective_max_bitrate_bps"]["samples"], 2)
        self.assertEqual(group["effective_min_bitrate_bps"]["min"], 20000)
        self.assertEqual(group["effective_target_bitrate_bps"]["max"], 4000000)

    def test_unknown_and_reused_threads_are_explicit(self):
        report = analyze(
            [
                line(1, "Drop Frame: target bitrate 500000, input frame rate 30"),
                line(2, "content_type: kScreenshare"),
                line(2, "content_type: kRealtimeVideo"),
            ]
        )
        self.assertEqual(report["observations"][0]["content_types"], [])
        self.assertEqual(
            report["observations"][1]["content_types"], ["kScreenshare", "kRealtimeVideo"]
        )
        self.assertEqual(analyze(["garbage"])["observations"], [])

    def test_quality_measurement_configuration_stays_with_its_encoder_thread(self):
        secret = "private-opaque-configuration"
        report = analyze(
            [
                line(10, "content_type: kRealtimeVideo"),
                line(20, "content_type: kScreenshare"),
                line(
                    10,
                    "Creating video encoder for: H264 { video-quality-measurement="
                    f"imageQualityVmaf_v061:5000,imageQualityWebrtcPsnrDb:5000,{secret} }}",
                    "encoder_factory.cpp",
                ),
                line(
                    20,
                    "Creating video encoder for: H264 { video-quality-measurement= }",
                    "encoder_factory.cpp",
                ),
            ]
        )
        camera, share = report["observations"]
        self.assertEqual(
            camera["events"],
            {
                "quality_measurement_configurations": 1,
                "vmaf_measurement_configurations": 1,
                "psnr_measurement_configurations": 1,
            },
        )
        self.assertEqual(share["events"], {})
        self.assertNotIn(secret, json.dumps(report))

    def test_unrelated_or_partial_measurement_tokens_are_not_scored(self):
        report = analyze(
            [
                line(
                    10,
                    "Creating video encoder for: H264 { video-quality-measurement="
                    "imageQualityVmaf_v061:5000-private,not-imageQualityWebrtcPsnrDb:1 }",
                    "encoder_factory.cpp",
                ),
                line(
                    20,
                    "video-quality-measurement=imageQualityVmaf_v061:5000",
                    "encoder_factory.cpp",
                ),
            ]
        )
        self.assertEqual(len(report["observations"]), 1)
        self.assertEqual(
            report["observations"][0]["events"], {"quality_measurement_configurations": 1}
        )

    def test_file_read_is_bounded_and_does_not_mutate_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "log"
            original = line(1, "Drop Frame: target bitrate 500000, input frame rate 30").encode()
            path.write_bytes(original + b"partial")
            self.assertEqual(list(read_logs([path])), [original.decode()])
            self.assertEqual(path.read_bytes(), original + b"partial")
            # An overlong line cannot smuggle a valid header in its tail.
            path.write_bytes(b"x" * 65537 + original + original)
            self.assertEqual(list(read_logs([path])), [original.decode()])


if __name__ == "__main__":
    unittest.main()
