import copy
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_screenshare import (
    display_device,
    external_pipewire,
    pipeline_evidence,
    render_device,
    validate_report,
)


class ScreenshareFixtureValidation(unittest.TestCase):
    def test_external_producer_requires_explicit_socket_and_node(self):
        self.assertEqual(external_pipewire(None, None, "pipewire"), [])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "remote"
            with socket.socket(socket.AF_UNIX) as remote:
                remote.bind(str(path))
                self.assertEqual(
                    external_pipewire(path, 42, "pipewire"),
                    ["--ro-bind", str(path), "/run/dpe-external-pipewire"],
                )
                for args in (
                    (path, None, "pipewire"),
                    (None, 42, "pipewire"),
                    (path, 0, "pipewire"),
                    (path, True, "pipewire"),
                    (path, 42, "x11"),
                ):
                    with self.subTest(args=args), self.assertRaises(ValueError):
                        external_pipewire(*args)
            path.unlink()
            path.touch()
            with self.assertRaises(ValueError):
                external_pipewire(path, 42, "pipewire")

    def test_gpu_input_does_not_hide_cpu_rendition_fallback(self):
        evidence = pipeline_evidence(
            [
                "Successfully selected GPU for DMABUF: synthetic device",
                "Video frame parameters changed: dimensions=1920x1088, texture=1.",
                "Failed to find rendition for requested resolution 1680x952",
                "Failed to find rendition for requested resolution 1680x952",
            ]
        )
        self.assertTrue(evidence["gpu_import_observed"])
        self.assertEqual(evidence["native_frame_dimensions"], ["1920x1088"])
        self.assertEqual(evidence["gpu_rendition_fallbacks_in_retained_log"], 2)

    def setUp(self):
        self.report = {
            "passed": True,
            "measurements": {"phases": {"initial": {"uniqueFrames": 90}}},
            "config": {"encoder": "h264-vaapi"},
            "samples": [
                {
                    "sent": {
                        "framesEncoded": 90,
                        "codecName": "H264",
                        "encoderImplementationName": "amd: vaapi",
                    }
                },
            ],
        }
        self.observations = [{"content_types": ["kScreenshare"], "is_hardware_accelerated": True}]

    def test_hevc_requires_actual_hevc_and_a_real_receiver(self):
        report = copy.deepcopy(self.report)
        report["config"].update(encoder="h265-vaapi", decoder="electron")
        sent = report["samples"][0]["sent"]
        sent["codecName"] = "H265"
        validate_report(report, self.observations)
        self.assertTrue(report["validation"]["backend_verified"])
        self.assertFalse(report["validation"]["decoder_verified"])
        report["samples"][0]["received"] = {
            "framesDecoded": 90,
            "decoderImplementationName": "electron",
        }
        validate_report(report, self.observations)
        self.assertTrue(report["validation"]["decoder_verified"])
        sent["codecName"] = "H264"
        validate_report(report, self.observations)
        self.assertFalse(report["validation"]["codec_verified"])
        self.assertFalse(report["validation"]["backend_verified"])

    def test_delivery_with_unrequested_padding_fails_framing(self):
        for dimensions, expected in (
            (["1920x1080"], True),
            (["1920x1080", "1486x836"], True),
            (["1920x1082"], False),
            (["1920x1088"], False),
            (["1984x1080"], False),
            ([], False),
        ):
            with self.subTest(dimensions=dimensions):
                report = copy.deepcopy(self.report)
                report["config"].update(width=1920, height=1080)
                report["measurements"]["phases"]["initial"]["dimensions"] = dimensions
                validation = validate_report(report, self.observations)["validation"]
                self.assertEqual(validation["framing_integrity_verified"], expected)

    def test_framing_uses_explicit_encoder_geometry_and_exact_resolution_gate(self):
        self.report["config"].update(width=1920, height=1080, encode_width=1280, encode_height=720)
        self.report["measurements"]["phases"]["initial"]["dimensions"] = ["1920x1080"]
        validate_report(self.report, self.observations)
        self.assertFalse(self.report["validation"]["framing_integrity_verified"])
        self.report["measurements"]["phases"]["initial"]["dimensions"] = ["1280x720", "990x556"]
        self.report["config"]["expect_resolution"] = "1280x720"
        validate_report(self.report, self.observations)
        self.assertTrue(self.report["validation"]["framing_integrity_verified"])
        self.assertFalse(self.report["validation"]["dimensions_verified"])

    def test_hardware_requires_codec_implementation_and_native_hardware_evidence(self):
        for change in (
            "codec",
            "implementation",
            "hardware",
            "mixed-content",
            "missing",
            "fallback",
        ):
            with self.subTest(change=change):
                report, observations = copy.deepcopy(self.report), copy.deepcopy(self.observations)
                sample = report["samples"][0]["sent"]
                if change == "codec":
                    sample["codecName"] = "VP8"
                elif change == "implementation":
                    sample["encoderImplementationName"] = "OpenH264"
                elif change == "hardware":
                    observations[0]["is_hardware_accelerated"] = False
                elif change == "mixed-content":
                    observations[0]["content_types"].append("kRealtimeVideo")
                elif change == "missing":
                    report["samples"] = [{"sent": None}]
                else:
                    report["samples"].append(
                        {"sent": {**sample, "encoderImplementationName": "OpenH264"}}
                    )
                self.assertFalse(validate_report(report, observations)["passed"])

    def test_delivery_experiment_requires_matching_runtime_evidence(self):
        self.report["config"].update(capture_delivery="fast", fps=60)
        validate_report(self.report, self.observations)
        self.assertFalse(self.report["validation"]["capture_delivery_verified"])
        self.report["captureDelivery"] = [
            {"fast": True, "requestedFps": 60, "callbacks": 400, "forwarded": 100, "dropped": 300}
        ]
        validate_report(self.report, self.observations)
        self.assertTrue(self.report["validation"]["capture_delivery_verified"])
        for key, bad in (("fast", False), ("requestedFps", 240), ("dropped", 0)):
            report = copy.deepcopy(self.report)
            report["captureDelivery"][0][key] = bad
            validate_report(report, self.observations)
            self.assertFalse(report["validation"]["capture_delivery_verified"])

    def test_correct_backend_does_not_turn_delivery_failure_into_success(self):
        self.report["passed"] = False
        report = validate_report(self.report, self.observations)
        self.assertTrue(report["validation"]["backend_verified"])
        self.assertFalse(report["passed"])

    def test_hardware_pass_does_not_claim_performance_improvement(self):
        report = validate_report(self.report, self.observations)
        self.assertTrue(report["passed"])
        self.assertFalse(report["performance_improvement_verified"])

    def test_explicit_sync_requires_native_consumption_and_producer_release(self):
        self.report["config"].update(sync_timeline=True, sync_delay_ms=10)
        self.report["config"]["capture_sync"] = True
        self.report["captureSync"] = {
            "installed": True,
            "correctedWaits": 90,
            "clockFailures": 0,
            "waitErrors": 0,
        }
        self.report["captureAdapter"] = {
            "frames": 90,
            "syncTimelineFrames": 90,
            "syncTimelineInvalidFrames": 0,
            "noSyncTimelineFrames": 0,
        }
        self.report["native_explicit_sync_observed"] = True
        self.report["syncProducer"] = {
            "queued": 90,
            "acquired": 90,
            "released": 82,
            "delay_ms": 10,
        }
        self.assertTrue(validate_report(copy.deepcopy(self.report), self.observations)["passed"])
        for field, value in (
            ("installed", False),
            ("correctedWaits", 0),
            ("clockFailures", 1),
            ("waitErrors", 1),
        ):
            report = copy.deepcopy(self.report)
            report["captureSync"][field] = value
            self.assertFalse(validate_report(report, self.observations)["passed"])
        for field, value in (("released", 0), ("acquired", 20), ("delay_ms", 0)):
            report = copy.deepcopy(self.report)
            report["syncProducer"][field] = value
            self.assertFalse(validate_report(report, self.observations)["passed"])
        for field in ("syncTimelineInvalidFrames", "noSyncTimelineFrames"):
            report = copy.deepcopy(self.report)
            report["captureAdapter"][field] = 1
            self.assertFalse(validate_report(report, self.observations)["passed"])
        self.report["native_explicit_sync_observed"] = False
        self.assertFalse(validate_report(self.report, self.observations)["passed"])

    def test_software_baseline_is_valid_but_needs_screenshare_evidence(self):
        self.report["config"]["encoder"] = "vp8"
        self.report["samples"][0]["sent"].update(
            codecName="VP8", encoderImplementationName="libvpx"
        )
        self.observations[0]["is_hardware_accelerated"] = False
        self.assertTrue(validate_report(self.report, self.observations)["passed"])
        self.assertFalse(validate_report(self.report, [])["passed"])

    def test_gpu_access_requires_explicit_render_device(self):
        self.assertEqual(render_device(None), [])
        for path in (Path("/dev/null"), Path("/dev/dri/card0"), Path(__file__)):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    render_device(path)

    def test_host_display_cannot_be_used_as_capture_source(self):
        self.assertEqual(display_device("x11", "x11"), [])
        with self.assertRaisesRegex(ValueError, "restricted to synthetic"):
            display_device("wayland", "x11")

    def test_av1_requires_actual_hardware_encoder_and_compatible_codec_name(self):
        self.report["config"]["encoder"] = "av1-vaapi"
        self.report["samples"][0]["sent"]["codecName"] = "AV1X"
        self.assertTrue(validate_report(self.report, self.observations)["passed"])
        self.report["samples"][0]["sent"]["encoderImplementationName"] = "libaom"
        self.assertFalse(validate_report(self.report, self.observations)["passed"])

    def test_guard_fallback_requires_native_event_and_distinct_negotiated_payload(self):
        self.report["config"].update(encoder="av1-vaapi", encoder_guard=True, fallback_h264=True)
        self.report["encoderGuard"] = {"installed": True, "rejectedCreates": 1}
        self.report["codecFallbacks"] = [{"kind": "encoder", "codec": "AV1X"}]
        self.report["negotiation"] = [{"codec": "H264", "payloadType": 103}]
        self.assertTrue(validate_report(copy.deepcopy(self.report), self.observations)["passed"])
        for field in ("installed", "rejection", "callback", "payload", "codec"):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                if field == "installed":
                    report["encoderGuard"]["installed"] = False
                elif field == "rejection":
                    report["encoderGuard"]["rejectedCreates"] = 0
                elif field == "callback":
                    report["codecFallbacks"] = []
                    report["codecErrors"] = [{"kind": "sender", "error": "AV1"}]
                elif field == "payload":
                    report["negotiation"][0]["payloadType"] = 101
                else:
                    report["samples"][0]["sent"]["codecName"] = "AV1X"
                self.assertFalse(validate_report(report, self.observations)["passed"])

    def test_production_transport_requires_observed_active_media_and_idle_cleanup(self):
        self.report["config"].update(
            transport_adapter=True, backend="pipewire", fps=30, source_fps=240
        )
        self.report["pipewire_streaming_observed"] = True
        self.report["captureAdapter"] = {"pacingFps": 0}
        self.report["transportAdapter"] = {
            "active": {
                "active": True,
                "actualCodec": "H264",
                "requestedFps": 30,
                "hardware": "hardware",
                "inputFps": 30,
                "outputFps": 30,
                "bitrate": 6000000,
                "captureMethod": "pipewire",
                "captureCounters": {"frames": 100, "targetFps": 30, "paced": 70},
            },
            "idle": {"active": False},
        }
        self.assertTrue(validate_report(copy.deepcopy(self.report), self.observations)["passed"])
        for field in ("active", "actualCodec", "inputFps", "captureMethod", "idle", "pacingFps"):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                if field == "idle":
                    report["transportAdapter"]["idle"] = {"active": True}
                elif field == "pacingFps":
                    report["captureAdapter"][field] = 30
                else:
                    report["transportAdapter"]["active"][field] = None
                self.assertFalse(validate_report(report, self.observations)["passed"])

    def test_gpu_factory_label_without_decoded_frames_does_not_verify_hardware_viewing(self):
        self.report["config"]["decoder"] = "electron"
        received = {"framesDecoded": 0, "decoderImplementationName": "electron"}
        self.report["samples"][0]["received"] = received
        self.assertFalse(validate_report(self.report, self.observations)["passed"])
        self.report["passed"] = True
        received["framesDecoded"] = 60
        self.assertTrue(validate_report(self.report, self.observations)["passed"])
        received["decoderImplementationName"] = "dav1d"
        self.assertFalse(validate_report(self.report, self.observations)["passed"])

    def test_scaler_install_without_exercising_the_native_call_is_not_verified(self):
        self.report["config"]["capture_scale"] = True
        self.report["captureScale"] = {"installed": True, "calls": 0, "invalidWants": 0}
        self.assertFalse(validate_report(copy.deepcopy(self.report), self.observations)["passed"])
        self.report["captureScale"]["calls"] = 2
        self.assertTrue(validate_report(copy.deepcopy(self.report), self.observations)["passed"])
        self.report["captureScale"]["invalidWants"] = 1
        self.assertFalse(validate_report(copy.deepcopy(self.report), self.observations)["passed"])

    def test_network_bottleneck_requires_delivery_feedback_drops_and_cleanup(self):
        self.report["config"]["network_profile"] = "bottleneck"
        self.report["network"] = dict(
            senderIdentified=True,
            receiverIdentified=True,
            reverseFeedbackPackets=30,
            phases={
                phase: {"deliveredPackets": 300, "droppedPackets": 10}
                for phase in ("initial", "constrained", "recovery")
            },
            maximumQueuedBytes=100000,
            maximumQueueDelayMs=99.9,
            maximumDeliveryLatenessMs=5,
            closed=True,
            queuedPackets=0,
            queuedBytes=0,
        )
        self.assertTrue(validate_report(copy.deepcopy(self.report), self.observations)["passed"])
        for field, value in (
            ("senderIdentified", False),
            ("reverseFeedbackPackets", 0),
            ("phases", {}),
            ("maximumQueuedBytes", 300000),
            ("maximumQueueDelayMs", 101),
            ("maximumDeliveryLatenessMs", 30),
            ("closed", False),
            ("queuedPackets", 1),
        ):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["network"][field] = value
                self.assertFalse(validate_report(report, self.observations)["passed"])

    def test_software_decode_and_exact_visible_dimensions_are_checked_separately(self):
        self.report["config"].update(decoder="software", expect_resolution="1920x1080")
        self.report["samples"][0]["received"] = {
            "framesDecoded": 90,
            "decoderImplementationName": "ffmpeg",
        }
        phase = self.report["measurements"]["phases"]["initial"]
        phase["dimensions"] = ["1920x1080"]
        self.assertTrue(validate_report(self.report, self.observations)["passed"])
        phase["dimensions"] = ["1920x1088"]
        self.assertFalse(validate_report(self.report, self.observations)["passed"])

    def test_documents_frame_rate_has_a_bounded_delivery_threshold(self):
        self.report["config"].update(fps=5, seconds=6)
        self.report["measurements"]["phases"]["initial"]["uniqueFrames"] = 12
        self.assertTrue(validate_report(self.report, self.observations)["passed"])

    def test_delivery_without_valid_markers_is_not_a_timing_pass(self):
        self.report["measurements"] = {"phases": {}}
        report = validate_report(self.report, self.observations)
        self.assertTrue(report["validation"]["delivery_verified"])
        self.assertFalse(report["validation"]["timing_verified"])
        self.assertFalse(report["passed"])

    def test_many_unreadable_markers_cannot_bias_latency_into_a_pass(self):
        self.report["measurements"]["invalidMarkers"] = 20
        self.assertFalse(validate_report(self.report, self.observations)["passed"])

    def test_requested_capture_memory_and_adapter_must_actually_be_exercised(self):
        self.report["config"].update(backend="pipewire", capture_mode="latest", memory="dmabuf")
        self.report["pipewire_streaming_observed"] = True
        self.report["captureAdapter"] = dict(
            installed=True, latestEnabled=True, frames=90, dmabufFrames=90
        )
        self.assertTrue(validate_report(self.report, self.observations)["passed"])
        for field, value in (
            ("dmabufFrames", 0),
            ("frames", 91),
            ("returnErrors", 1),
            ("installed", False),
            ("latestEnabled", False),
        ):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["captureAdapter"][field] = value
                self.assertFalse(validate_report(report, self.observations)["passed"])
        self.report["pipewire_streaming_observed"] = False
        self.assertFalse(validate_report(self.report, self.observations)["passed"])


if __name__ == "__main__":
    unittest.main()
