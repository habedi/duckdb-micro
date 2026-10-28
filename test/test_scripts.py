"""Host regressions for the feature check and shell build entry point."""

import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("features", ROOT / "scripts/check-features.py")
features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(features)


class ScriptTests(unittest.TestCase):
    def setUp(self):
        features.pending = b""

    def test_echo_does_not_count_as_sql_result(self):
        sql = "SELECT json_extract('{\"score\": 99}', '$.score');"
        port = Mock()
        port.read.side_effect = [sql.encode() + b"\r\n", b"SQL error: missing function\r\nduckdb> "]
        with self.assertRaises(RuntimeError):
            features.exec_duckdb(port, io.BytesIO(), sql, b"\n99\t")
        self.assertEqual(port.read.call_count, 2)

    def test_sql_error_after_result_is_rejected(self):
        port = Mock()
        port.read.side_effect = [b"\n99\t\r\n", b"SQL error: failed\r\nduckdb> "]
        with self.assertRaises(RuntimeError):
            features.exec_duckdb(port, io.BytesIO(), "SELECT 99;", b"\n99\t")

    def test_real_sql_result_passes(self):
        port = Mock()
        port.read.side_effect = [b"SELECT 99;\r\n99\t\r\n1 row(s)\r\nduckdb> "]
        features.exec_duckdb(port, io.BytesIO(), "SELECT 99;", b"\n99\t")
        self.assertEqual(features.pending, b"")

    def test_feature_run_uses_unique_files_and_result_markers(self):
        serial = MagicMock()
        serial.SerialException = OSError
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(sys.modules, {"serial": serial}), \
                patch.object(sys, "argv", ["check-features.py", "fake-port"]), \
                patch.object(features, "log", Path(directory) / "capture.log"), \
                patch.object(features.time, "sleep"), \
                patch.object(features, "wait_for"), \
                patch.object(features, "exec_msh") as msh, \
                patch.object(features, "exec_duckdb") as sql, \
                patch("builtins.print"):
            fixtures = []
            for _ in range(2):
                msh.reset_mock()
                features.main()
                commands = [call.args[2] for call in msh.call_args_list]
                writes = [cmd.split()[1] for cmd in commands if cmd.startswith("write ")]
                removes = [cmd.split()[1] for cmd in commands if cmd.startswith("rm ")]
                self.assertTrue(all(name.startswith("/data/duckdb-features-") for name in writes))
                self.assertTrue(set(writes).issubset(removes))
                fixtures.extend(removes)
            self.assertEqual(len(fixtures), len(set(fixtures)))
            for call in sql.call_args_list:
                query, marker = call.args[2:]
                self.assertTrue(marker.startswith(b"\n"))
                self.assertNotIn(marker, query.encode())

    def test_shell_build_refreshes_existing_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copyfile(ROOT / "scripts/build-shell.sh", root / "scripts/build-shell.sh")
            (root / "scripts/build-probe.sh").write_text("touch build/refreshed\n")
            (root / "build/duckdb/src").mkdir(parents=True)
            (root / "build/duckdb/src/libduckdb_esp32p4.a").touch()
            sdk = root / "external/esp-idf"
            sdk.mkdir(parents=True)
            (sdk / "export.sh").write_text("idf.py() { test -f build/refreshed; }\n")
            subprocess.run(["bash", str(root / "scripts/build-shell.sh")], check=True)
            self.assertTrue((root / "build/refreshed").is_file())

    def test_clean_targets_preserve_tools_logs_backups_and_sources(self):
        for target in ("clean", "clean-all"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copyfile(ROOT / "Makefile", root / "Makefile")
                preserved = ("build/espressif/tool", "build/hardware/backup.bin",
                             "build/build.log", "external/duckdb/source.cpp")
                generated = ("build/test/test_binary", "build/duckdb/library.a",
                             "build/firmware/firmware.bin", "build/shell/shell.bin")
                for name in preserved + generated:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("fixture")
                for _ in range(2):
                    subprocess.run(["make", target], cwd=root, check=True, capture_output=True)
                for name in preserved:
                    self.assertEqual((root / name).read_text(), "fixture")
                for name in generated:
                    removed = target == "clean-all" or name.startswith("build/test/")
                    self.assertEqual((root / name).exists(), not removed)


if __name__ == "__main__":
    unittest.main()
