"""Host checks for stress-test memory thresholds and serial result validation."""

import argparse
import csv
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stress", ROOT / "scripts/check-stress.py")
stress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stress)

MEMORY = (b"Memory Tier            Total       Free   Min Free  Max Block\r\n"
          b"Internal SRAM        400000     300000     250000     200000\r\n"
          b"DMA-capable          350000     280000     230000     190000\r\n"
          b"SPI RAM (PSRAM)    32000000   30000000   20000000   28000000\r\n")


class StressTests(unittest.TestCase):
    def setUp(self):
        stress.features.pending = b""

    def test_memory_parse_and_missing_or_invalid_metrics(self):
        sample = stress.memory_sample(MEMORY)
        self.assertEqual(sample["Internal SRAM"],
                         dict(total=400000, free=300000, minimum=250000, largest=200000))
        self.assertEqual(sample["SPI RAM (PSRAM)"]["largest"], 28000000)
        for output in (b"free\r\nmsh> ", MEMORY.replace(b"300000", b"100000"),
                       MEMORY.replace(b"28000000", b"0")):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                stress.memory_sample(output)

    def test_sustained_loss_of_free_memory_or_largest_block_fails(self):
        baseline = stress.memory_sample(MEMORY)
        for tier in stress.TIERS:
            for metric in ("free", "largest"):
                sample = stress.memory_sample(MEMORY)
                sample[tier][metric] = int(sample[tier][metric] * 0.9)
                streaks = {}
                with self.subTest(tier=tier, metric=metric):
                    for _ in range(2):
                        stress.check_memory(baseline, sample, streaks, 5)
                    with self.assertRaisesRegex(RuntimeError, "3 consecutive"):
                        stress.check_memory(baseline, sample, streaks, 5)

    def test_transient_loss_recovers_and_minimum_free_is_not_a_leak(self):
        baseline = stress.memory_sample(MEMORY)
        low = stress.memory_sample(MEMORY)
        low["Internal SRAM"]["largest"] //= 2
        recovered = stress.memory_sample(MEMORY)
        recovered["Internal SRAM"]["minimum"] = 1
        streaks = {}
        for _ in range(5):
            stress.check_memory(baseline, low, streaks, 5)
            stress.check_memory(baseline, recovered, streaks, 5)

    def test_serial_rejects_echo_wrong_rows_and_errors_after_result(self):
        for output in (b"SELECT 42;\r\nduckdb> ", b"\n142\t\r\nduckdb> ",
                       b"\n42\t\r\nSQL error: failed\r\nduckdb> ",
                       b"Guru Meditation\r\nduckdb> "):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                port = Mock()
                port.read.return_value = output
                stress.command(port, io.BytesIO(), "SELECT 42;", b"duckdb> ", "42")
            stress.features.pending = b""

    def test_split_serial_result_and_timeout(self):
        port = Mock()
        port.read.side_effect = [b"SELECT 42;\r\n4", b"2\t\r\nduck", b"db> "]
        stress.command(port, io.BytesIO(), "SELECT 42;", b"duckdb> ", "42")
        self.assertEqual(stress.features.pending, b"")
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            stress.command(port, io.BytesIO(), "free", timeout=0)

    def test_prompt_redraw_during_echo_does_not_finish_command(self):
        port = Mock()
        port.read.side_effect = [b"\r\x1b[0Kduckdb> S\r\x1b[9CELECT 42;\r\n",
                                 b"answer\t\r\n42\t\r\n1 row(s)\r\nduckdb> "]
        stress.command(port, io.BytesIO(), "SELECT 42;", b"duckdb> ", "42")
        self.assertEqual(port.read.call_count, 2)
        port.read.side_effect = [b"\r\x1b[0Kduckdb> BEGIN TRANSACTION;\r\n",
                                 b"0 row(s)\r\nduckdb> "]
        stress.command(port, io.BytesIO(), "BEGIN TRANSACTION;", b"duckdb> ")
        self.assertEqual(port.read.call_count, 4)

    def test_workloads_reopen_persistent_data_and_reject_corruption(self):
        port = Mock()
        committed = []
        corrupt = False

        def respond(data):
            line = data.decode().strip()
            result = None
            prompt = b"duckdb> "
            if line == ".quit":
                prompt = b"msh> "
            elif line.startswith("CREATE TABLE samples") or line.startswith("COPY samples"):
                result = "1000"
            elif line.startswith("SELECT count(*), sum(") and "FROM cycles" not in line:
                result = "1000\t499500"
            elif line.startswith("SELECT json_extract"):
                result = "99\t3"
            elif line.startswith("SELECT sum(score)"):
                result = "99"
            elif line.startswith("INSERT INTO cycles"):
                committed.append(int(line.split("(")[1].split(")")[0]))
                result = "1"
            elif line == "SELECT count(*), sum(id) FROM cycles;":
                result = f"{len(committed)}\t{0 if corrupt else sum(committed)}"
            output = data + b"\n"
            if result is not None:
                output += result.encode() + b"\t\r\n"
            port.read.return_value = output + prompt

        port.write.side_effect = respond
        for cycle in (1, 2):
            stress.workload(port, io.BytesIO(), "/sdcard/unique", cycle)
        self.assertEqual(committed, [1, 2])
        self.assertEqual(sum(call.args[0] == b"duckdb /sdcard/unique.duckdb\r"
                             for call in port.write.call_args_list), 4)
        corrupt = True
        with self.assertRaisesRegex(RuntimeError, "Unexpected result"):
            stress.workload(port, io.BytesIO(), "/sdcard/unique", 3)

    def test_run_warms_up_records_csv_and_removes_only_owned_files(self):
        args = argparse.Namespace(warmup=1, cycles=3, duration=None, max_loss_percent=5)
        report = io.StringIO()
        with patch.object(stress, "workload") as workload, \
                patch.object(stress, "command", return_value=MEMORY) as command, \
                patch("builtins.print"):
            stress.run(Mock(), io.BytesIO(), report, args, "/sdcard/unique")
        self.assertEqual([call.args[-1] for call in workload.call_args_list], [1, 2, 3, 4])
        rows = list(csv.DictReader(io.StringIO(report.getvalue())))
        self.assertEqual(len(rows), 8)
        self.assertEqual([row["phase"] for row in rows], ["warmup"] * 2 + ["measured"] * 6)
        self.assertAlmostEqual(float(rows[0]["fragmentation_percent"]), 33.333)
        self.assertEqual(rows[0]["free_bytes"], "300000")
        self.assertEqual([call.args[2] for call in command.call_args_list[-3:]],
                         ["rm /sdcard/unique.json", "rm /sdcard/unique.parquet",
                          "rm /sdcard/unique.duckdb"])

    def test_duration_run_measures_at_least_three_cycles(self):
        args = argparse.Namespace(warmup=1, cycles=20, duration=0.1, max_loss_percent=5)
        with patch.object(stress, "workload") as workload, \
                patch.object(stress, "command", return_value=MEMORY), \
                patch.object(stress.time, "monotonic", side_effect=range(20)), \
                patch("builtins.print"):
            stress.run(Mock(), io.BytesIO(), io.StringIO(), args, "/data/unique")
        self.assertEqual(workload.call_count, 4)

    def test_workload_failure_keeps_fixtures_and_stops(self):
        args = argparse.Namespace(warmup=1, cycles=3, duration=None, max_loss_percent=5)
        with patch.object(stress, "workload", side_effect=RuntimeError("bad checksum")), \
                patch.object(stress, "command") as command:
            with self.assertRaisesRegex(RuntimeError, "bad checksum"):
                stress.run(Mock(), io.BytesIO(), io.StringIO(), args, "/sdcard/unique")
        self.assertFalse(any(call.args[2].startswith("rm ") for call in command.call_args_list))

    def test_invalid_arguments_are_rejected(self):
        for args in (("--cycles", "2"), ("--warmup", "0"), ("--duration", "nan"),
                     ("--duration", "inf"), ("--max-loss-percent", "100")):
            with self.subTest(args=args), patch("sys.stderr", io.StringIO()), \
                    self.assertRaises(SystemExit):
                stress.arguments(["fake-port", *args])


if __name__ == "__main__":
    unittest.main()
