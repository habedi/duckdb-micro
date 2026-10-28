"""Host regressions for the feature check and shell build entry point."""

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
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
                             "build/firmware/firmware.bin", "build/shell/shell.bin",
                             "build/duckdb-micro-shell.tar.gz", "build/duckdb-micro-probe.tar.gz")
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

    def test_espidf_install_includes_clang_in_the_same_tools_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "Makefile", root / "Makefile")
            sdk = root / "external/esp-idf"
            (sdk / "tools").mkdir(parents=True)
            (sdk / "install.sh").write_text(
                'test "$1" = esp32p4\n'
                'mkdir -p "$IDF_TOOLS_PATH"\n'
                'touch "$IDF_TOOLS_PATH/base-tools"\n')
            (sdk / "tools/idf_tools.py").write_text(
                'import os, sys\nfrom pathlib import Path\n'
                'assert sys.argv[1:] == ["install", "esp-clang"]\n'
                'tools = Path(os.environ["IDF_TOOLS_PATH"])\n'
                'assert (tools / "base-tools").is_file()\n'
                '(tools / "clang").touch()\n')
            tools = root / "custom tools"
            env = dict(os.environ, IDF_TOOLS_PATH=str(tools))
            subprocess.run(["make", "espidf-install", f"PYTHON={sys.executable}"],
                           cwd=root, env=env, check=True, capture_output=True)
            self.assertTrue((tools / "clang").is_file())

    def test_flash_targets_reset_the_board(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "Makefile", root / "Makefile")
            sdk = root / "sdk"
            sdk.mkdir()
            (sdk / "export.sh").write_text(
                'python() { printf "%s\\n" "$@" > "$IDF_TOOLS_PATH/flash-call"; }\n')
            for name in ("shell", "firmware"):
                build = root / "build" / name
                build.mkdir(parents=True)
                (build / "flash_args").touch()
            env = dict(os.environ, IDF_PATH=str(sdk), IDF_TOOLS_PATH=str(root))
            for target in ("flash", "probe-flash"):
                with self.subTest(target=target):
                    subprocess.run(["make", target, "TTY=fake-port"], cwd=root,
                                   env=env, check=True, capture_output=True)
                    arguments = (root / "flash-call").read_text().splitlines()
                    self.assertEqual(arguments[arguments.index("--after") + 1], "hard-reset")
                    self.assertEqual(arguments[arguments.index("--port") + 1], "fake-port")
                    self.assertEqual(arguments[-2:], ["write-flash", "@flash_args"])

    def test_firmware_packages_include_all_flashing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "Makefile", root / "Makefile")
            for build, target in (("shell", "shell"), ("firmware", "probe")):
                names = ("flash_args", f"duckdb_{target}.bin", "bootloader/bootloader.bin",
                         "partition_table/partition-table.bin")
                for name in names:
                    path = root / "build" / build / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(name)
            subprocess.run(["make", "firmware-package"], cwd=root,
                           check=True, capture_output=True)
            for target in ("shell", "probe"):
                with tarfile.open(root / f"build/duckdb-micro-{target}.tar.gz") as archive:
                    names = {"flash_args", f"duckdb_{target}.bin", "bootloader/bootloader.bin",
                             "partition_table/partition-table.bin"}
                    self.assertEqual(set(archive.getnames()), names)
                    for name in names:
                        self.assertEqual(archive.extractfile(name).read().decode(), name)

    def test_tag_release_creates_or_updates_only_drafts(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text()
        step = workflow.split("      - name: Create or update draft release\n", 1)[1]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        for state in ("missing", "draft", "published"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                gh = root / "gh"
                gh.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
                    import json, os, sys
                    from pathlib import Path
                    with Path("calls.jsonl").open("a") as log:
                        log.write(json.dumps(sys.argv[1:]) + "\\n")
                    if sys.argv[1:3] == ["release", "view"]:
                        state = os.environ["RELEASE_STATE"]
                        if state == "missing":
                            sys.exit(1)
                        print("true" if state == "draft" else "false")
                    '''))
                gh.chmod(0o755)
                (root / "firmware").mkdir()
                (root / "firmware/shell.tar.gz").touch()
                env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                           RELEASE_STATE=state, RELEASE_TAG="v1.2.3")
                subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=root,
                               env=env, check=True, capture_output=True)
                calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
                expected = ["view", "create", "upload"] if state == "missing" else (
                    ["view", "upload"] if state == "draft" else ["view"])
                self.assertEqual([call[1] for call in calls], expected)
                self.assertTrue(all(call[2] == "v1.2.3" for call in calls))
                if state == "missing":
                    self.assertIn("--draft", calls[1])
                    self.assertIn("--verify-tag", calls[1])
                if state != "published":
                    self.assertIn("--clobber", calls[-1])
                    self.assertIn("firmware/shell.tar.gz", calls[-1])


if __name__ == "__main__":
    unittest.main()
