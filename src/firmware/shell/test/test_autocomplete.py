"""Exercise shell completion with the SDK's real parser and line editor."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


RUNNER = shlex.split(os.environ.get("TEST_RUNNER", ""))


ROOT = Path(__file__).resolve().parents[4]

STUBS = r'''
#define _DEFAULT_SOURCE
#include <assert.h>
#include <dirent.h>
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wsign-compare"
#pragma GCC diagnostic ignored "-Wunused-result"
#include "linenoise.c"
#pragma GCC diagnostic pop
#include "split_argv.c"
#include "path_util.h"
static char s_cwd[256];
static bool resolve_path(const char *in, char *out, size_t size)
{ return path_resolve(s_cwd, in, out, size); }
static void esp_console_get_completion(const char *buf, linenoiseCompletions *lc)
{
  const char *commands[] = {"cat", "cd", "cp"};
  for (size_t i = 0; i < 3; i++)
    if (strncmp(commands[i], buf, strlen(buf)) == 0)
      linenoiseAddCompletion(lc, commands[i]);
}
'''

CHECKS = r'''
static ssize_t backspace_read(int fd, void *buf, size_t len)
{ (void)fd; (void)len; *(char *)buf = 127; return 1; }

int main(int argc, char **argv)
{
  assert(argc >= 3);
  if (strcmp(argv[2], "clear-screen") == 0) {
    assert(cmd_clear(0, NULL) == 0);
    return 0;
  }
  snprintf(s_cwd, sizeof(s_cwd), "%s", argv[1]);
  linenoiseCompletions lc = {0, NULL};
  custom_completion(argv[2], &lc);
  for (size_t i = 0; i < lc.len; i++) {
    assert(strlen(lc.cvec[i]) < SHELL_MAX_CMDLINE - 1);
    if (argc >= 4) {
      char parsed[SHELL_MAX_CMDLINE];
      strcpy(parsed, lc.cvec[i]);
      char *args[8];
      size_t count = esp_console_split_argv(parsed, args, 8);
      assert(count == (size_t)atoi(argv[4]));
      assert(strcmp(args[count - 1], argv[3]) == 0);
    }
    if (argc == 6) {
      struct linenoiseState l = {0};
      l.buf = calloc(SHELL_MAX_CMDLINE, 1);
      l.buflen = SHELL_MAX_CMDLINE - 1;
      strcpy(l.buf, argv[2]); l.len = l.pos = strlen(l.buf);
      l.prompt = "msh> "; l.plen = 5; l.cols = 80;
      completionCallback = custom_completion;
      read_func = backspace_read;
      assert(completeLine(&l) == 127);
      assert(l.len == strlen(l.buf));
      linenoiseEditBackspace(&l);
      assert(l.len == strlen(l.buf));
      free(l.buf);
    } else {
      puts(lc.cvec[i]);
    }
  }
  freeCompletions(&lc);
  return 0;
}
'''


class AutocompleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        work = Path(cls.directory.name)
        source = (ROOT / "src/firmware/shell/main/main.c").read_text()
        limit = next(line for line in source.splitlines()
                     if line.startswith("#define SHELL_MAX_CMDLINE "))
        cls.line_limit = int(limit.split()[-1])
        start = source.index("static void add_path_completion(")
        end = source.index("static void register_commands(", start)
        harness = work / "completion.c"
        harness.write_text(STUBS + limit + "\n" + source[start:end] + CHECKS)
        (work / "sdkconfig.h").write_text("#define CONFIG_IDF_TARGET_LINUX 1\n")
        cls.binary = work / "test_completion"
        command = shlex.split(os.environ.get("CC", "cc"))
        for name in ("CFLAGS", "SANFLAGS"):
            command += shlex.split(os.environ.get(name, ""))
        console = ROOT / "external/esp-idf/components/console"
        command += ["-std=c17", "-I" + str(work),
                    "-I" + str(console), "-I" + str(console / "linenoise"),
                    "-I" + str(ROOT / "src/firmware/shell/main"),
                    str(harness), str(ROOT / "src/firmware/shell/main/path_util.c"),
                    "-o", str(cls.binary)]
        command += shlex.split(os.environ.get("LDFLAGS", ""))
        subprocess.run(command, check=True)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cwd = Path(directory.name)

    def complete(self, text, expected=None, argc=2, edit=False, cwd=None):
        command = [*RUNNER,
                   str(self.binary), str(cwd or self.cwd), text]
        if expected is not None:
            command += [expected, str(argc)]
        if edit:
            command += ["edit"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_commands_and_mounts(self):
        self.assertEqual(self.complete("ca"), ["cat"])
        self.assertEqual(self.complete("cd /s", "/sdcard/"), ["cd /sdcard/"])
        self.assertEqual(self.complete("cd s", "/sdcard/", cwd="/"), ["cd /sdcard/"])
        self.assertEqual(self.complete("cd /d", "/data/"), ["cd /data/"])
        self.assertEqual(self.complete("cd d", "/data/", cwd="/"), ["cd /data/"])
        self.assertEqual(self.complete("cd /x"), [])
        self.assertEqual(self.complete("cd x", cwd="/"), [])

    def test_clear_screen(self):
        result = subprocess.run([*RUNNER, str(self.binary), str(self.cwd), "clear-screen"],
                                capture_output=True, check=True)
        self.assertEqual(result.stdout, b"\x1b[H\x1b[2J")

    def test_spaces_quotes_and_backslashes_round_trip(self):
        for name in ('my file.txt', 'my"quote.txt', 'my\\file.txt'):
            with self.subTest(name=name):
                path = self.cwd / name
                path.touch()
                suggestions = self.complete("cat my", name)
                self.assertEqual(len(suggestions), 1)
                path.unlink()

    def test_quoted_and_escaped_input(self):
        (self.cwd / "my file.txt").touch()
        for text in ('cat "my f', 'cat my\\ f', 'cat "my f"'):
            with self.subTest(text=text):
                self.assertEqual(self.complete(text, "my file.txt"),
                                 ["cat my\\ file.txt"])

    def test_directory_and_second_argument(self):
        folder = self.cwd / "my dir"
        folder.mkdir()
        (folder / "file.txt").touch()
        self.assertEqual(self.complete('cd "my', "my dir/"), ["cd my\\ dir/"])
        for text in ('cat "my dir/fi', 'cat my\\ dir/fi'):
            self.assertEqual(self.complete(text, "my dir/file.txt"),
                             ["cat my\\ dir/file.txt"])
        self.assertEqual(self.complete('cp "source file" my', "my dir/", argc=3),
                         ['cp "source file" my\\ dir/'])

    def test_prefix_beyond_127_characters(self):
        prefix = "a" * 127
        (self.cwd / (prefix + "x.txt")).touch()
        (self.cwd / (prefix + "y.txt")).touch()
        self.assertEqual(self.complete("cat " + prefix + "x", prefix + "x.txt"),
                         ["cat " + prefix + "x.txt"])

    def test_line_limit_and_backspace(self):
        name = "z" * 250
        (self.cwd / name).touch()
        self.assertEqual(self.complete("cat z", name), ["cat " + name])
        self.complete("cat z", name, edit=True)
        (self.cwd / name).unlink()
        for length in (251, 255):
            path = self.cwd / ("z" * length)
            path.touch()
            prefix = "cp " + "s" * (self.line_limit - 200) + " "
            self.assertEqual(self.complete(prefix + "z"), [])
            path.unlink()

    def test_limit_includes_escaping_and_directory_slash(self):
        (self.cwd / ("a " * 90)).touch()
        prefix = "cp " + "s" * (self.line_limit - 200) + " "
        self.assertEqual(self.complete(prefix + "a"), [])
        (self.cwd / ("d" * 251)).mkdir()
        self.assertEqual(self.complete(prefix + "d"), [])
