/* Host unit tests for path_resolve logic. */

#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "path_util.h"

static int s_tests_run = 0;
static int s_tests_failed = 0;

#define TEST_ASSERT(cond) do { \
    s_tests_run++; \
    if (!(cond)) { \
      printf("FAIL: %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      s_tests_failed++; \
    } \
  } while (0)

#define TEST_RESOLVE(cwd, in, expected) do { \
    char out[512]; \
    bool ok = path_resolve(cwd, in, out, sizeof(out)); \
    TEST_ASSERT(ok); \
    if (ok) { \
      if (strcmp(out, expected) != 0) { \
        printf("FAIL: path_resolve(\"%s\", \"%s\") => \"%s\", expected \"%s\"\n", \
               cwd ? cwd : "NULL", in ? in : "NULL", out, expected); \
        s_tests_failed++; \
      } \
    } \
    s_tests_run++; \
  } while (0)

static void test_basic_paths(void)
{
  TEST_RESOLVE("/sdcard", "test.txt", "/sdcard/test.txt");
  TEST_RESOLVE("/sdcard", "/data/app.bin", "/data/app.bin");
  TEST_RESOLVE("/sdcard", "", "/sdcard");
  TEST_RESOLVE("/sdcard", NULL, "/sdcard");
  TEST_RESOLVE("/", "", "/");
  TEST_RESOLVE("/", "/", "/");
}

static void test_dot_and_dotdot(void)
{
  TEST_RESOLVE("/sdcard", ".", "/sdcard");
  TEST_RESOLVE("/sdcard/sub", "..", "/sdcard");
  TEST_RESOLVE("/sdcard", "..", "/");
  TEST_RESOLVE("/sdcard", "../..", "/");
  TEST_RESOLVE("/sdcard", "../../../../../..", "/");
  TEST_RESOLVE("/sdcard", "a/b/../c/./d", "/sdcard/a/c/d");
  TEST_RESOLVE("/data", "dir/..", "/data");
  TEST_RESOLVE("/data", "dir/../..", "/");
}

static void test_slashes_and_normalization(void)
{
  TEST_RESOLVE("/sdcard", "///a////b///", "/a/b");
  TEST_RESOLVE("/sdcard/", "file.txt", "/sdcard/file.txt");
  TEST_RESOLVE("/sdcard//sub/", "foo", "/sdcard/sub/foo");
  TEST_RESOLVE(NULL, "relative", "/relative");
  TEST_RESOLVE("", "relative", "/relative");
}

static void test_buffer_limits(void)
{
  char untouched = 'x';
  TEST_ASSERT(!path_resolve("/", "file", &untouched, 0));
  TEST_ASSERT(untouched == 'x');
  TEST_ASSERT(!path_resolve("/", "file", NULL, 0));
  TEST_ASSERT(!path_resolve("/", "/", NULL, 0));
  TEST_ASSERT(!path_resolve("/", "", NULL, 0));

  char out[8];
  /* Output "/sdcard" requires 8 bytes including null terminator */
  bool ok = path_resolve("/", "sdcard", out, sizeof(out));
  TEST_ASSERT(ok);
  TEST_ASSERT(strcmp(out, "/sdcard") == 0);

  /* Output "/sdcard1" requires 9 bytes; 8 bytes must fail */
  ok = path_resolve("/", "sdcard1", out, sizeof(out));
  TEST_ASSERT(!ok);

  /* Huge path exceeding internal 512-byte buffer */
  char huge[600];
  memset(huge, 'a', sizeof(huge) - 1);
  huge[sizeof(huge) - 1] = '\0';
  char large_out[1024];
  ok = path_resolve("/sdcard", huge, large_out, sizeof(large_out));
  TEST_ASSERT(!ok);

  /* More than 64 segments */
  char many_segs[512] = "";
  for (int i = 0; i < 70; i++)
    {
      strcat(many_segs, "/x");
    }
  ok = path_resolve("/", many_segs, large_out, sizeof(large_out));
  TEST_ASSERT(!ok);
}

int main(void)
{
  test_basic_paths();
  test_dot_and_dotdot();
  test_slashes_and_normalization();
  test_buffer_limits();

  printf("Path resolution tests: %d checks, %d failures\n",
         s_tests_run, s_tests_failed);
  return s_tests_failed == 0 ? 0 : 1;
}
