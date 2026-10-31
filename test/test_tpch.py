"""Host regression checks for the TPC-H subset runner."""

import argparse
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_stress import MEMORY


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tpch", ROOT / "scripts/check-tpch.py")
tpch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tpch)


class TpchTests(unittest.TestCase):
    def setUp(self):
        tpch.stress.features.pending = b""

    def test_pinned_queries_fit_shell_and_do_not_modify_data(self):
        for number in tpch.QUERIES:
            sql = tpch.query_sql(number)
            self.assertTrue(sql.startswith("SELECT "))
            self.assertNotIn(";", sql)
            self.assertLess(len(sql.encode()) + 1, 1024)

    def test_insertion_batches_quote_values_limit_bytes_and_preserve_rows(self):
        source = [dict(text="a'b" + "x" * 300, empty=None, number="42") for _ in range(10)]
        batches = list(tpch.insertion_batches('a"b', source))
        self.assertEqual(sum(count for _, count in batches), 10)
        self.assertTrue(all(sql.startswith('INSERT INTO "a""b" VALUES ') for sql, _ in batches))
        self.assertTrue(all(len(sql.encode()) < 1000 for sql, _ in batches))
        self.assertIn("a''b", batches[0][0])
        self.assertIn(",NULL,'42')", batches[0][0])
        self.assertEqual(list(tpch.insertion_batches("empty", [])), [])
        for value in ("x" * 1000, "line\nbreak", "interrupt\x03", "tab\t"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                list(tpch.insertion_batches("table", [dict(value=value)]))

    def test_load_creates_tables_in_transactions_and_verifies_counts(self):
        args = argparse.Namespace(database="/sdcard/unique.duckdb", timeout=1,
                                  manifest=Path("fixture/manifest.json"), duckdb="host-duckdb")
        responses = [[dict(table_name="t", sql="CREATE TABLE t(i INT);")],
                     [dict(i="1"), dict(i="2")]]
        with patch.object(tpch, "host_query", side_effect=responses), \
                patch.object(tpch, "send_command", return_value=b"msh> ") as command, \
                patch("builtins.print"):
            tpch.load_fixture(Mock(), io.BytesIO(), args)
        lines = [call.args[2] for call in command.call_args_list]
        self.assertEqual(lines, ["ls /sdcard", "duckdb --compact /sdcard/unique.duckdb",
                                 "CREATE TABLE t(i INT);", "BEGIN TRANSACTION;",
                                 'INSERT INTO "t" VALUES (\'1\'),(\'2\');', "COMMIT;",
                                 'SELECT count(*) FROM "t";', "CHECKPOINT;", ".quit"])
        with patch.object(tpch, "send_command", return_value=b"unique.duckdb") as command:
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                tpch.load_fixture(Mock(), io.BytesIO(), args)
            self.assertEqual(command.call_count, 1)

    def test_long_commands_are_paced_without_losing_or_duplicating_bytes(self):
        port = Mock()
        line = "SELECT '" + "x" * 200 + "';"
        with patch.object(tpch.time, "sleep") as sleep, \
                patch.object(tpch.stress, "command", return_value=b"done") as command:
            self.assertEqual(tpch.send_command(port, io.BytesIO(), line), b"done")
        chunks = [call.args[0] for call in port.write.call_args_list]
        self.assertTrue(all(len(chunk) == 64 for chunk in chunks))
        self.assertEqual(b"".join(chunks) + command.call_args.args[2].encode(), line.encode())
        self.assertEqual(sleep.call_count, len(chunks))

    def test_load_bounds_transaction_and_session_state(self):
        args = argparse.Namespace(database="/sdcard/unique.duckdb", timeout=1,
                                  manifest=Path("fixture/manifest.json"), duckdb="host-duckdb")
        with patch.object(tpch, "host_query", side_effect=[
                [dict(table_name="t", sql="CREATE TABLE t(i INT);")], [dict(i="1")] * 41]), \
                patch.object(tpch, "insertion_batches", return_value=[("INSERT 1;", 1)] * 41), \
                patch.object(tpch, "send_command", return_value=b"msh> ") as command, \
                patch("builtins.print"):
            tpch.load_fixture(Mock(), io.BytesIO(), args)
        lines = [call.args[2] for call in command.call_args_list]
        self.assertEqual(lines.count("COMMIT;"), 3)
        self.assertEqual(lines.count("BEGIN TRANSACTION;"), 3)
        self.assertEqual(lines.count("duckdb --compact /sdcard/unique.duckdb"), 3)
        self.assertEqual(lines.count(".quit"), 3)
        streak = 0
        for line in lines:
            if line == "INSERT 1;":
                streak += 1
                self.assertLessEqual(streak, 20)
            elif line == "COMMIT;":
                streak = 0

    def test_run_automatically_prepares_loads_and_cleans_only_its_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = argparse.Namespace(manifest=root / "fixture/manifest.json", scale=None,
                                      duckdb="host-duckdb", database=None, tty="fake",
                                      repeats=1, timeout=1, queries=tpch.DEFAULT_QUERIES,
                                      memory_limit_mb=8, threads=1)
            manifest = dict(scale=0.001, database_sha256=hashlib.sha256(b"fixture").hexdigest(),
                            queries=[dict(number=q, sql="SELECT 42", rows=[["42"]])
                                     for q in tpch.QUERIES])

            def prepare(options):
                options.output.mkdir()
                (options.output / "tpch.duckdb").write_bytes(b"fixture")
                (options.output / "manifest.json").write_text(json.dumps(manifest))

            serial = Mock()
            serial.return_value.__enter__ = Mock(return_value=Mock())
            serial.return_value.__exit__ = Mock(return_value=False)
            with patch.object(tpch, "ROOT", root), \
                    patch.object(tpch, "prepare", side_effect=prepare) as prep, \
                    patch.object(tpch, "query_sql", return_value="SELECT 42"), \
                    patch.dict("sys.modules", {"serial": Mock(Serial=serial)}), \
                    patch.object(tpch.stress.features, "wait_for"), \
                    patch.object(tpch.time, "sleep"), \
                    patch.object(tpch, "load_fixture") as load, \
                    patch.object(tpch, "run_queries") as queries, \
                    patch.object(tpch, "send_command", return_value=b"msh> ") as command, \
                    patch("builtins.print"):
                tpch.run(args)
                prep.assert_called_once()
                load.assert_called_once()
                queries.assert_called_once()
                self.assertTrue(args.database.startswith("/sdcard/tpch-"))
                self.assertEqual(command.call_args.args[2], f"rm {args.database}")
                queries.side_effect = RuntimeError("bad query")
                args.database = None
                command.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "bad query"):
                    tpch.run(args)
                command.assert_not_called()
                queries.side_effect = None
                args.database = "/sdcard/existing.duckdb"
                load.reset_mock()
                tpch.run(args)
                load.assert_not_called()
                command.assert_not_called()
                args.database = None
                (args.manifest.parent / "tpch.duckdb").write_bytes(b"changed")
                serial.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "digest differs"):
                    tpch.run(args)
                serial.assert_not_called()

    def test_result_parser_ignores_echo_and_rejects_truncated_output(self):
        output = (b"\r\x1b[0Kduckdb> SELECT 42;\r\nanswer\t\r\n42\t\r\n"
                  b"1 row(s), 15 ms\r\nduckdb> ")
        self.assertEqual(tpch.result_rows(output), ([["42"]], 15))
        self.assertEqual(tpch.result_rows(b"answer\t\r\n0 row(s), 0 ms\r\n"), ([], 0))
        for bad in (b"42\t\r\nduckdb> ", b"answer\t\r\n2 row(s), 15 ms\r\n",
                    b"answer\t\r\n42\t\r\n26 row(s), 15 ms (first 25 shown)\r\n"):
            with self.subTest(output=bad), self.assertRaises(RuntimeError):
                tpch.result_rows(bad)

    def test_rows_preserve_order_nulls_and_numeric_tolerance(self):
        tpch.check_rows([["A", "NULL", "1.0000000001", "0.000000001"]],
                        [["A", None, "1", "0"]])
        for actual in ([["B", "NULL", "1", "0"]], [["A", "NULL", "1.01", "0"]],
                       [["A", "NULL", "NaN", "0"]], [["A", "NULL", "Infinity", "0"]],
                       [["A", "NULL", "1"]], [], [["A", "0", "1", "0"]]):
            with self.subTest(rows=actual), self.assertRaises(RuntimeError):
                tpch.check_rows(actual, [["A", None, "1", "0"]])
        with self.assertRaises(RuntimeError):
            tpch.check_rows([["2"], ["1"]], [["1"], ["2"]])

    def test_prepare_records_version_strings_and_refuses_existing_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(output=Path(folder) / "fixture", scale=0.001,
                                      duckdb="host-duckdb")

            def host(cli, database, sql, readonly=True):
                if "pragma_version" in sql:
                    return [dict(library_version="v2.0.0", source_id="pinned")]
                if "dbgen" in sql:
                    Path(database).write_bytes(b"fixture")
                    self.assertFalse(readonly)
                    return []
                self.assertIn("COLUMNS(*)::VARCHAR", sql)
                self.assertTrue(readonly)
                return [dict(answer="42.00", empty=None)]

            with patch.object(tpch, "host_query", side_effect=host), patch("builtins.print"):
                tpch.prepare(args)
                with self.assertRaises(FileExistsError):
                    tpch.prepare(args)
            manifest = json.loads((args.output / "manifest.json").read_text())
            self.assertEqual(manifest["host_version"]["source_id"], "pinned")
            self.assertEqual(manifest["scale"], 0.001)
            self.assertEqual(len(manifest["database_sha256"]), 64)
            self.assertEqual([q["number"] for q in manifest["queries"]], list(tpch.QUERIES))
            self.assertEqual(manifest["queries"][0]["rows"], [["42.00", None]])

    def test_serial_run_checks_each_repeat_and_reports_after_session_close(self):
        port = Mock()
        corrupt = False

        def respond(data):
            line = data.decode().strip()
            prompt = b"duckdb> "
            if line == "free":
                port.read.return_value = MEMORY + b"msh> "
                return
            result = b"\n"
            if line == ".quit":
                prompt = b"msh> "
            elif line.startswith("SELECT"):
                result += (b"answer\t\r\n" + (b"99" if corrupt else b"42") +
                           b"\t\r\n1 row(s), 17 ms\r\n")
            port.read.return_value = data + result + prompt

        port.write.side_effect = respond
        args = argparse.Namespace(database="/sdcard/tpch.duckdb", repeats=2, timeout=1,
                                  queries=tpch.DEFAULT_QUERIES, memory_limit_mb=12, threads=2)
        manifest = dict(queries=[dict(number=6, sql="SELECT 42", rows=[["42"]]),
                                 dict(number=3, sql="SELECT 99", rows=[["99"]]),
                                 dict(number=14, sql="SELECT 99", rows=[["99"]])])
        report = io.StringIO()
        with patch("builtins.print"):
            tpch.run_queries(port, io.BytesIO(), report, manifest, args)
        rows = list(csv.DictReader(io.StringIO(report.getvalue())))
        self.assertEqual([row["phase"] for row in rows], ["warmup"] * 2 + ["measured"] * 4)
        self.assertEqual({row["query_ms"] for row in rows}, {"17"})
        self.assertEqual({row["tier"] for row in rows}, set(tpch.stress.TIERS))
        commands = [call.args[0].decode().strip() for call in port.write.call_args_list]
        self.assertEqual(commands.count("SELECT 42;"), 3)
        self.assertEqual(commands.count("SET memory_limit='12MB';"), 3)
        self.assertEqual(commands.count("SET threads=2;"), 3)
        self.assertEqual({row["minimum_free_bytes"] for row in rows}, {"250000", "20000000"})
        self.assertEqual(commands.count("ATTACH '/sdcard/tpch.duckdb' AS tpch (READ_ONLY);"), 3)
        self.assertEqual(commands.count(".quit"), 3)
        self.assertEqual(commands.count("free"), 3)
        self.assertFalse(any(command.startswith(("rm ", "write ")) for command in commands))
        corrupt = True
        with self.assertRaisesRegex(RuntimeError, "Result mismatch"):
            tpch.run_queries(port, io.BytesIO(), io.StringIO(), manifest, args)

    def test_sql_error_stops_without_recording_a_success(self):
        port = Mock()
        port.read.return_value = b"\nSQL error: out of memory\r\nduckdb> "
        report = io.StringIO()
        args = argparse.Namespace(database="/sdcard/tpch.duckdb", repeats=1, timeout=1,
                                  queries=tpch.DEFAULT_QUERIES, memory_limit_mb=8, threads=1)
        with self.assertRaisesRegex(RuntimeError, "Firmware fault"):
            tpch.run_queries(port, io.BytesIO(), report, dict(queries=[dict(number=1)]), args)
        self.assertEqual(len(report.getvalue().splitlines()), 1)

    def test_arguments_reject_unsafe_paths_and_invalid_limits(self):
        self.assertEqual(tpch.arguments(["run", "tty"]).queries, tpch.DEFAULT_QUERIES)
        self.assertEqual(tpch.arguments(["run", "tty"]).memory_limit_mb, 8)
        self.assertEqual(tpch.arguments(["run", "tty"]).threads, 1)
        self.assertEqual(tpch.arguments(["run", "tty", "--queries", "3"]).queries, [3])
        for options in (("--database", "/sdcard/a'; DROP TABLE t;--.duckdb"),
                        ("--database", "/data/tpch.duckdb"), ("--repeats", "0"),
                        ("--timeout", "nan"), ("--timeout", "inf"), ("--timeout", "0"),
                        ("--queries", "22"), ("--memory-limit-mb", "0"),
                        ("--memory-limit-mb", "24"), ("--threads", "0"), ("--threads", "3")):
            with self.subTest(options=options), patch("sys.stderr", io.StringIO()), \
                    self.assertRaises(SystemExit):
                tpch.arguments(["run", "tty", "manifest.json", *options])
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            tpch.arguments(["prepare", "--scale", "1"])

    def test_format_bytes_handles_nulls_scales_and_details(self):
        self.assertEqual(tpch.format_bytes(None), "N/A")
        self.assertEqual(tpch.format_bytes(500), "500 B")
        self.assertEqual(tpch.format_bytes(2048), "2.0 KB")
        self.assertEqual(tpch.format_bytes(1048576 * 2), "2.00 MB")
        self.assertEqual(tpch.format_bytes_detail(None), "N/A")
        self.assertEqual(tpch.format_bytes_detail(2048), "2,048 B (2.0 KB)")

    def test_parse_report_data_and_generate_markdown_with_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            csv_path = root / "tpch-test.csv"
            json_path = root / "tpch-test.json"

            csv_content = (
                "query,phase,iteration,query_ms,tier,free_bytes,minimum_free_bytes,"
                "largest_block_bytes\n"
                "1,warmup,0,100,Internal SRAM,500000,100000,250000\n"
                "1,warmup,0,100,SPI RAM (PSRAM),30000000,20000000,25000000\n"
                "1,measured,1,90,Internal SRAM,500000,100000,250000\n"
                "1,measured,1,90,SPI RAM (PSRAM),30000000,20000000,25000000\n"
                "1,measured,2,92,Internal SRAM,500000,100000,250000\n"
                "1,measured,2,92,SPI RAM (PSRAM),30000000,20000000,25000000\n"
            )
            csv_path.write_text(csv_content)

            meta = {
                "manifest": {
                    "scale": 0.001,
                    "host_version": {"library_version": "v1.5.5", "codename": "Variegata"},
                    "database_sha256": "fakehash",
                    "queries": [{"number": 1, "sql": "SELECT 1;", "rows": [["1"]]}],
                },
                "database": "/sdcard/tpch-test.duckdb",
                "repeats": 2,
                "memory_limit_mb": 8,
                "threads": 1,
            }
            json_path.write_text(json.dumps(meta))

            queries_data, parsed_meta = tpch.parse_report_data(csv_path, json_path)
            self.assertIn(1, queries_data)
            self.assertEqual(queries_data[1]["warmup_ms"], 100)
            self.assertEqual(queries_data[1]["measured_ms"], [90, 92])
            self.assertEqual(parsed_meta["manifest"]["scale"], 0.001)

            markdown = tpch.generate_markdown_report(csv_path, json_path)
            self.assertIn("# TPC-H Benchmark Report", markdown)
            self.assertIn("## Benchmark Environment", markdown)
            self.assertIn("## Performance Summary", markdown)
            self.assertIn("## Memory Statistics", markdown)
            self.assertIn("## Detailed Iteration Results", markdown)
            self.assertIn("## Query Definitions", markdown)
            self.assertIn("Pricing Summary Report", markdown)
            self.assertIn("| Q01 | Pricing Summary Report | 100 | 90 | 91.0 | 92 |", markdown)
            self.assertIn("**Total**", markdown)
            self.assertIn("SELECT 1;", markdown)

    def test_generate_markdown_without_json_and_without_min_free(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            csv_path = root / "tpch-old.csv"
            csv_content = (
                "query,phase,iteration,query_ms,tier,free_bytes,largest_block_bytes\n"
                "6,warmup,0,200,Internal SRAM,490000,240000\n"
                "6,warmup,0,200,SPI RAM (PSRAM),31000000,28000000\n"
                "6,measured,1,195,Internal SRAM,490000,240000\n"
                "6,measured,1,195,SPI RAM (PSRAM),31000000,28000000\n"
            )
            csv_path.write_text(csv_content)

            markdown = tpch.generate_markdown_report(csv_path, None)
            self.assertIn("# TPC-H Benchmark Report", markdown)
            self.assertIn("Forecasting Revenue Change", markdown)
            self.assertIn("SRAM Final Free", markdown)
            self.assertNotIn("SRAM Low Watermark", markdown)
            self.assertIn("| Q06 |", markdown)

    def test_parse_report_data_rejects_missing_or_empty_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            missing = root / "missing.csv"
            with self.assertRaisesRegex(RuntimeError, "Report CSV file not found"):
                tpch.parse_report_data(missing)

            empty = root / "empty.csv"
            empty.write_text("")
            with self.assertRaisesRegex(RuntimeError, "Report CSV file has no data rows"):
                tpch.parse_report_data(empty)

    def test_resolve_report_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            csv_a = root / "tpch-aaa.csv"
            csv_a.write_text("dummy")
            csv_b = root / "tpch-bbb.csv"
            csv_b.write_text("dummy")
            json_b = root / "tpch-bbb.json"
            json_b.write_text("{}")

            resolved_csv, resolved_json = tpch.resolve_report_paths(hardware_dir=root)
            self.assertEqual(resolved_csv, csv_b)
            self.assertEqual(resolved_json, json_b)

            resolved_csv, resolved_json = tpch.resolve_report_paths("tpch-aaa", hardware_dir=root)
            self.assertEqual(resolved_csv, csv_a)
            self.assertIsNone(resolved_json)

            resolved_csv, resolved_json = tpch.resolve_report_paths("bbb", hardware_dir=root)
            self.assertEqual(resolved_csv, csv_b)
            self.assertEqual(resolved_json, json_b)

            resolved_csv, _ = tpch.resolve_report_paths(str(csv_a), hardware_dir=root)
            self.assertEqual(resolved_csv, csv_a)

            with self.assertRaisesRegex(RuntimeError, "benchmark file not found"):
                tpch.resolve_report_paths("nonexistent", hardware_dir=root)

            empty_dir = root / "empty_dir"
            empty_dir.mkdir()
            with self.assertRaisesRegex(RuntimeError, "No TPC-H benchmark results found"):
                tpch.resolve_report_paths(hardware_dir=empty_dir)

    def test_report_command_execution_and_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            csv_path = root / "tpch-run.csv"
            csv_path.write_text(
                "query,phase,iteration,query_ms,tier,free_bytes,minimum_free_bytes,"
                "largest_block_bytes\n"
                "1,warmup,0,100,Internal SRAM,500000,100000,250000\n"
                "1,warmup,0,100,SPI RAM (PSRAM),30000000,20000000,25000000\n"
                "1,measured,1,90,Internal SRAM,500000,100000,250000\n"
                "1,measured,1,90,SPI RAM (PSRAM),30000000,20000000,25000000\n"
            )

            # Test writing to custom output file
            custom_out = root / "custom.md"
            args = argparse.Namespace(input=csv_path, input_opt=None, output=custom_out,
                                      stdout=False, quiet=False)
            with patch("sys.stderr", io.StringIO()):
                tpch.report(args)
            self.assertTrue(custom_out.is_file())
            self.assertIn("# TPC-H Benchmark Report", custom_out.read_text())

            # Test writing to stdout only via -
            args = argparse.Namespace(input=csv_path, input_opt=None, output=Path("-"),
                                      stdout=False, quiet=False)
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                tpch.report(args)
            self.assertIn("# TPC-H Benchmark Report", stdout.getvalue())

            # Test default output path
            default_out = root / "tpch-run.md"
            self.assertFalse(default_out.exists())
            args = argparse.Namespace(input=csv_path, input_opt=None, output=None,
                                      stdout=False, quiet=True)
            with patch("sys.stderr", io.StringIO()):
                tpch.report(args)
            self.assertTrue(default_out.is_file())

    def test_arguments_report_subcommand(self):
        parsed = tpch.arguments(["report"])
        self.assertEqual(parsed.action, "report")
        self.assertIsNone(parsed.input)
        self.assertIsNone(parsed.output)
        self.assertFalse(parsed.stdout)
        self.assertFalse(parsed.quiet)

        parsed = tpch.arguments(["report", "my-run.csv", "-o", "out.md", "--stdout", "-q"])
        self.assertEqual(parsed.input, Path("my-run.csv"))
        self.assertEqual(parsed.output, Path("out.md"))
        self.assertTrue(parsed.stdout)
        self.assertTrue(parsed.quiet)


if __name__ == "__main__":
    unittest.main()
