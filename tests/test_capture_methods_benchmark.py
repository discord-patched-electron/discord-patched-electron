import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_capture_methods import (
    Run,
    capture_threads,
    counters,
    descendants,
    device_memory,
    direct_command,
    drm_memory,
    memory_proportion,
    needs_fullscreen,
    peer_identity,
    producer_failures,
    split_peer_samples,
    summarize_resources,
)


class CaptureMethodsBenchmark(unittest.TestCase):
    def test_producer_teardown_failure_overrides_delivery_success(self):
        clean = [
            {"event": "ready"},
            {"event": "ended", "failed": False, "errorCode": "", "frames": 2593},
            {"event": "stopped", "code": 0},
        ]
        self.assertEqual(producer_failures(clean), [])
        failures = [
            {"event": "ended", "failed": True, "errorCode": "direct-source-failed"},
            {"event": "stopped", "code": 1},
        ]
        self.assertEqual(producer_failures([*clean, *failures]), failures)

    def test_gpu_memory_units_aliases_and_shared_client_deduplication(self):
        self.assertEqual(
            drm_memory(
                {
                    "drm-memory-vram": "4 KiB",
                    "drm-resident-vram": "8 KiB",
                    "drm-shared-vram": "1 MiB",
                    "drm-total-memory": "123",
                    "drm-active-vram": "-1 KiB",
                    "drm-purgeable-vram": "2 ns",
                    "drm-total-cycles-gfx": "900",
                }
            ),
            {"drm-resident-vram": 8192, "drm-shared-vram": 1048576, "drm-total-memory": 123},
        )

        def sample(seconds, bytes):
            value = {
                "cpuTicks": 0,
                "startTicks": 1,
                "drmClients": {},
                "drmMemory": {"pci/1": {"drm-resident-vram": bytes}},
            }
            return {"seconds": seconds, "groups": {"sender": {"1": value, "2": value}}}

        result = summarize_resources([sample(2, 4096), sample(3, 8192)])["sender"]
        self.assertEqual(
            result["drmMemoryBytes"]["drm-resident-vram"],
            {"mean": 6144, "peak": 8192, "samples": 2},
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            device = root / "renderD128/device"
            device.mkdir(parents=True)
            (device / "mem_info_vram_used").write_text("1000\n")
            (device / "mem_info_vram_total").write_text("-1\n")
            self.assertEqual(device_memory("/dev/dri/renderD128", root), {"vramUsedBytes": 1000})

    def test_gpu_engine_counter_dips_hold_the_previous_high_water_mark(self):
        samples = []
        for seconds, ns in [(2, 0), (3, 2_000_000_000), (4, 1_000_000_000)]:
            value = {"cpuTicks": 0, "startTicks": 1, "drmClients": {"pci/1": {"gfx": ns}}}
            samples.append({"seconds": seconds, "groups": {"sender": {"1": value}}})
        self.assertEqual(summarize_resources(samples)["sender"]["drmEnginePercent"], {"gfx": 100})

    def test_proportional_memory_bounds_do_not_treat_unreadable_helpers_as_zero(self):
        def sample(seconds):
            return {
                "seconds": seconds,
                "groups": {
                    "helper": {
                        "1": {
                            "cpuTicks": 0,
                            "startTicks": 1,
                            "drmClients": {},
                            "rssBytes": 100,
                            "pssBytes": 80,
                        },
                        "2": {"cpuTicks": 0, "startTicks": 2, "drmClients": {}, "rssBytes": 40},
                    }
                },
            }

        result = summarize_resources([sample(2), sample(3)])["helper"]
        self.assertEqual(result["pssMeanLowerBytes"], 80)
        self.assertEqual(result["pssMeanUpperBytes"], 120)

    def test_unsampled_memory_is_not_zero_or_a_repeated_old_snapshot(self):
        samples = []
        for seconds, snapshot, pss in [(2, True, 80), (2.5, False, None), (4, True, 120)]:
            value = {"cpuTicks": 0, "startTicks": 1, "drmClients": {}, "rssBytes": 200}
            if pss is not None:
                value["pssBytes"] = pss
            samples.append(
                {
                    "seconds": seconds,
                    "memorySnapshot": snapshot,
                    "groups": {"sender": {"1": value}},
                }
            )
        result = summarize_resources(samples)["sender"]
        self.assertEqual(result["pssMeanLowerBytes"], 100)
        self.assertEqual(result["pssMeanUpperBytes"], 100)
        self.assertEqual(result["pssSamples"], 2)
        self.assertEqual(result["rssMeanBytes"], 200)

    def test_peer_identity_uses_the_namespace_and_preserves_shared_costs(self):
        peers = {
            "pidNamespace": "pid:[9]",
            "sender": 101,
            "receiver": 102,
            "browser": 1,
            "sharedGpu": [2],
        }
        processes = {
            str(i): {"namespacePid": nested, "pidNamespace": namespace}
            for i, (nested, namespace) in enumerate(
                [(101, "pid:[9]"), (102, "pid:[9]"), (2, "pid:[9]"), (101, "pid:[8]"), (None, None)]
            )
        }
        samples = [{"seconds": 0, "groups": {"native": processes, "helper": {}}}]
        divided = split_peer_samples(samples, peers)[0]["groups"]
        self.assertEqual(set(divided["native_sender"]), {"0"})
        self.assertEqual(set(divided["native_receiver"]), {"1"})
        self.assertEqual(set(divided["native_shared"]), {"2"})
        self.assertEqual(set(divided["native_other"]), {"3", "4"})
        self.assertIn("native", samples[0]["groups"])
        with self.assertRaises(ValueError):
            split_peer_samples(samples, dict(peers, receiver=101))
        with self.assertRaises(ValueError):
            split_peer_samples(samples, dict(peers, receiver=999))

    def test_thread_wait_and_proportional_memory_do_not_guess_missing_counters(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)
            root = proc / "7"
            (root / "ns").mkdir(parents=True)
            (root / "ns/pid").symlink_to("pid:[123]")
            (root / "status").write_text("NSpid:\t7 101\n")
            self.assertEqual(
                peer_identity(7, proc), {"pidNamespace": "pid:[123]", "namespacePid": 101}
            )
            (root / "smaps_rollup").write_text("Pss: 123 kB\n")
            self.assertEqual(memory_proportion(7, proc), {"pssBytes": 123 * 1024})
            fields = ["S", *(["0"] * 21)]
            fields[16], fields[19] = "-3", "55"
            for tid, name in [(7, "source"), (8, "DPE capture"), (9, "unrelated")]:
                task = root / "task" / str(tid)
                task.mkdir(parents=True)
                (task / "stat").write_text(f"{tid} ({name}) " + " ".join(fields))
                (task / "schedstat").write_text("1000 2000 3\n")
            threads = capture_threads(7, proc)
            self.assertEqual(set(threads), {"7", "8"})
            self.assertEqual(threads["8"]["runqueueWaitNs"], 2000)
            self.assertEqual(threads["8"]["nice"], -3)
            (root / "task/8/schedstat").write_text("-1 3 5\n")
            self.assertNotIn("8", capture_threads(7, proc))
            (root / "smaps_rollup").write_text("Pss: 123 bytes\n")
            self.assertEqual(memory_proportion(7, proc), {})

    def test_wayland_dry_run_requires_and_hashes_the_explicit_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = root / "input"
            fixture.write_text("fixture")
            command = [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts/benchmark_capture_methods.py"),
                "--method",
                "wayland",
                "--runtime",
                str(fixture),
                "--voice",
                str(fixture),
                "--workload",
                str(fixture),
                "--render-node",
                "/dev/dri/renderD128",
                "--monitor",
                "DP-2",
                "--output",
                str(root / "result"),
            ]
            missing = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("--wayland-helper", missing.stderr)
            ready = subprocess.run(
                command + ["--wayland-helper", str(fixture)],
                check=True,
                capture_output=True,
                text=True,
            )
            plan = json.loads(ready.stdout)
            self.assertEqual(plan["wayland_helper"], str(fixture))
            self.assertEqual(len(plan["inputs"][str(fixture)]), 64)
            self.assertFalse((root / "result").exists())
            rejected = subprocess.run(
                command + ["--wayland-helper", str(fixture), "--gl-semaphore-sync"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            for name in (
                "vkcapture-broker.py",
                "gamecapture-launch.py",
                "dpe-vkcapture-source",
                "libobs_glcapture.so",
                "libVkLayer_obs_vkcapture.so",
            ):
                (root / name).write_text("fixture")
            command[command.index("--method") + 1] = "game"
            copy = subprocess.run(
                command
                + ["--game-bundle", str(root), "--game-handoff", "copy", "--gl-semaphore-sync"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(json.loads(copy.stdout)["gl_semaphore_sync"])

    def test_direct_backends_keep_their_own_output_and_device_contracts(self):
        args = Namespace(
            method="wayland",
            wayland_helper="wayland-source",
            kms_helper="kms-source",
            fps=60,
            render_node="/dev/dri/renderD128",
        )
        self.assertEqual(
            direct_command(args, "DP-2"),
            ["wayland-source", "--output", "DP-2", "--fps", "60"],
        )
        args.method = "kms"
        self.assertEqual(
            direct_command(args, "kms-card1-413"),
            [
                "kms-source",
                "--output",
                "kms-card1-413",
                "--fps",
                "60",
                "--render-node",
                "/dev/dri/renderD128",
            ],
        )

    def test_rss_uses_simultaneous_totals_and_preserves_missing_measurements(self):
        def process(rss):
            return {"cpuTicks": 0, "startTicks": 1, "drmClients": {}, "rssBytes": rss}

        samples = [
            {"seconds": 2, "groups": {"native": {"1": process(100), "2": process(300)}}},
            {"seconds": 3, "groups": {"native": {"1": process(500), "2": process(100)}}},
            {"seconds": 4, "groups": {"native": {"1": process(None)}}},
        ]
        summary = summarize_resources(samples)["native"]
        self.assertEqual(summary["rssMeanBytes"], 500)
        self.assertEqual(summary["rssPeakBytes"], 600)
        missing = [dict(samples[2], seconds=2), dict(samples[2], seconds=3)]
        self.assertNotIn("rssMeanBytes", summarize_resources(missing)["native"])

    def test_fullscreen_workload_is_not_toggled_back_into_a_tile(self):
        self.assertFalse(needs_fullscreen({"layout": {"window_size": [1920, 1080]}}))
        self.assertTrue(needs_fullscreen({"layout": {"window_size": [945, 1018]}}))
        self.assertTrue(needs_fullscreen({}))

    def test_children_from_non_main_threads_are_included(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)
            for path, value in (
                ("1/task/1/children", "2"),
                ("1/task/9/children", "3"),
                ("3/task/3/children", "4"),
            ):
                file = proc / path
                file.parent.mkdir(parents=True)
                file.write_text(value)
            self.assertEqual(descendants(1, proc), {2, 3, 4})
            cache = {}
            self.assertEqual(descendants(1, proc, cache=cache), {2, 3, 4})
            (proc / "3/task/3/children").write_text("4 5")
            self.assertEqual(descendants(3, proc, cache=cache), {4})
            self.assertEqual(descendants(3, proc), {4, 5})

    def test_non_drm_descriptors_are_not_opened_or_counted_as_gpu_clients(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)
            root = proc / "7"
            (root / "fdinfo").mkdir(parents=True)
            (root / "fd").mkdir()
            fields = ["S", *(["0"] * 21)]
            (root / "stat").write_text("7 (fixture) " + " ".join(fields))
            for number in [4, 5]:
                (root / f"fd/{number}").touch()
                (root / f"fdinfo/{number}").write_text(
                    f"drm-client-id: {number}\ndrm-pdev: pci\ndrm-engine-gfx: 50 ns\n"
                )
            original_stat, original_read = Path.stat, Path.read_text
            reads = []

            def info(path, *args, **kwargs):
                if path == root / "fd/4":
                    return SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(226, 128))
                return original_stat(path, *args, **kwargs)

            def read(path, *args, **kwargs):
                reads.append(path)
                return original_read(path, *args, **kwargs)

            with patch.object(Path, "stat", info), patch.object(Path, "read_text", read):
                result = counters(7, proc)
            self.assertEqual(result["drmClients"], {"pci/4": {"drm-engine-gfx": 50}})
            self.assertIn(root / "fdinfo/4", reads)
            self.assertNotIn(root / "fdinfo/5", reads)

    def test_duplicate_drm_descriptors_and_process_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)
            (proc / "7/fdinfo").mkdir(parents=True)
            fields = ["S", *(["0"] * 21)]
            fields[11], fields[12], fields[19] = "20", "30", "300"
            fields[21] = "12"
            (proc / "7/stat").write_text("7 (process ) name) " + " ".join(fields))
            text = "drm-client-id: 5\ndrm-pdev: 0000:03:00.0\ndrm-engine-gfx: 50 ns\n"
            for fd in (4, 8):
                (proc / f"7/fdinfo/{fd}").write_text(text)
            actual = counters(7, proc)
            self.assertEqual(actual["cpuTicks"], 50)
            self.assertEqual(actual["rssBytes"], 12 * os.sysconf("SC_PAGE_SIZE"))
            self.assertEqual(actual["name"], "process ) name")
            self.assertEqual(actual["drmClients"], {"0000:03:00.0/5": {"drm-engine-gfx": 50}})
        first = {"cpuTicks": 10, "startTicks": 30, "drmClients": {"gpu/5": {"gfx": 0}}}
        last = {"cpuTicks": 110, "startTicks": 30, "drmClients": {"gpu/5": {"gfx": 100000000}}}
        reused = dict(last, cpuTicks=99000, startTicks=50)
        samples = [
            {"seconds": 2, "groups": {"native": {"1": first, "2": first}}},
            {"seconds": 3, "groups": {"native": {"1": last, "2": last}}},
            {"seconds": 4, "groups": {"native": {"1": reused}}},
        ]
        summary = summarize_resources(samples)["native"]
        self.assertEqual(summary["cpuOneCorePercent"], 200 / os.sysconf("SC_CLK_TCK") / 2 * 100)
        self.assertEqual(summary["drmEnginePercent"], {"gfx": 5})

    def test_coalesced_protocol_lines_do_not_wait_for_a_second_read_event(self):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                'import sys; print(\'{"id":1}\\n{"id":2}\', flush=True); sys.stdin.read()',
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        run = Run.__new__(Run)
        run.pending_lines, run.events = {}, []
        try:
            self.assertEqual(run.read(child, 2), {"id": 1})
            self.assertEqual(run.read(child, 0.05), {"id": 2})
        finally:
            child.communicate(timeout=2)


if __name__ == "__main__":
    unittest.main()
