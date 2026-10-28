"""Check shell copies and home paths using the SDK's real FAT implementation."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]
TEST = Path(__file__).resolve().parent


class FilesystemTests(unittest.TestCase):
    def test_copies_and_home_on_sd_card(self):
        source = (TEST.parent / "main/main.c").read_text()
        config = (TEST.parent / "sdkconfig.defaults").read_text()
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            defines = ["#define " + line.replace("=", " ").replace(" y", " 1")
                       for line in config.splitlines() if line.startswith("CONFIG_FATFS_")]
            (work / "sdkconfig.h").write_text("\n".join(defines) + "\n" +
                "#define CONFIG_FATFS_CODEPAGE 437\n"
                "#define CONFIG_FATFS_VOLUME_COUNT 1\n"
                "#define CONFIG_FATFS_PER_FILE_CACHE 1\n"
                "#define CONFIG_WL_SECTOR_SIZE 512\n")
            (work / "freertos").mkdir()
            (work / "freertos/FreeRTOS.h").write_text("")
            (work / "freertos/semphr.h").write_text("")
            mounts = source[source.index('#define SDCARD_MOUNT_POINT '):
                            source.index('#define SHELL_MAX_CMDLINE ')]
            cd = source[source.index('static int cmd_cd('):source.index('static int cmd_ls(')]
            cp = source[source.index('static int cmd_cp('):source.index('static int cmd_rm(')]
            (work / "shell_commands.inc").write_text(
                mounts + '\nstatic char s_cwd[256] = SHELL_HOME;\n' + cd + cp)
            fatfs = ROOT / "external/esp-idf/components/fatfs/src"
            for board in ("ESP32P4",):
                with self.subTest(board=board):
                    command = shlex.split(os.environ.get("CC", "cc"))
                    for name in ("CFLAGS", "SANFLAGS"):
                        command += shlex.split(os.environ.get(name, ""))
                    command += ["-I" + str(work), "-I" + str(fatfs),
                                "-I" + str(TEST.parent / "main"),
                                f"-DCONFIG_IDF_TARGET_{board}=1", str(TEST / "test_filesystem.c"),
                                str(TEST.parent / "main/path_util.c"),
                                str(fatfs / "ff.c"), str(fatfs / "ffunicode.c"),
                                "-o", str(work / "filesystem")]
                    command += shlex.split(os.environ.get("LDFLAGS", ""))
                    built = subprocess.run(command, capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    result = subprocess.run([str(work / "filesystem")],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
