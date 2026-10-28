/* Include the editor to exercise its internal logic without a serial port. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <stdlib.h>

static int allocations_before_failure = -1;

static int fail_allocation(void)
{
  if (allocations_before_failure < 0) return 0;
  if (allocations_before_failure-- > 0) return 0;
  errno = ENOMEM;
  return 1;
}

static void *test_malloc(size_t size)
{
  return fail_allocation() ? NULL : malloc(size);
}

static void *test_realloc(void *ptr, size_t size)
{
  return fail_allocation() ? NULL : realloc(ptr, size);
}

#define malloc test_malloc
#define realloc test_realloc
#include "../main/editor.c"
#undef malloc
#undef realloc

#include <assert.h>

static void reset_editor(void)
{
  for (int i = 0; i < E.numrows; i++) editorFreeRow(&E.row[i]);
  free(E.row);
  memset(&E, 0, sizeof(E));
}

static void test_long_filename(void)
{
  reset_editor();
  memset(E.filename, 'a', sizeof(E.filename) - 1);
  E.screencols = 80;
  struct abuf ab = ABUF_INIT;
  editorDrawStatusBar(&ab);
  assert(ab.len == 4 + E.screencols + 5);
  assert(ab.b[4] == ' ');
  for (int i = 5; i < 84; i++) assert(ab.b[i] == 'a');
  abFree(&ab);
}

static void test_long_line(const char *filename, int cr_position, bool crlf)
{
  reset_editor();
  char line[1024];
  memset(line, 'a', sizeof(line));
  if (cr_position >= 0) line[cr_position] = '\r';
  FILE *fp = fopen(filename, "w");
  assert(fp);
  assert(fwrite(line, 1, sizeof(line), fp) == sizeof(line));
  assert(fputs(crlf ? "\r\n" : "\n", fp) >= 0);
  assert(fputs("next\n", fp) >= 0);
  assert(fclose(fp) == 0);

  assert(editorOpenRows(filename) == 0);
  assert(E.numrows == 2);
  assert(E.row[0].size == sizeof(line));
  assert(memcmp(E.row[0].chars, line, sizeof(line)) == 0);
  assert(strcmp(E.row[1].chars, "next") == 0);
  assert(E.dirty == 0);
}

static void test_split_crlf(const char *filename)
{
  reset_editor();
  FILE *fp = fopen(filename, "w");
  assert(fp);
  for (int i = 0; i < 510; i++) assert(fputc('a', fp) == 'a');
  assert(fputs("\r\nend\r", fp) >= 0);
  assert(fclose(fp) == 0);

  assert(editorOpenRows(filename) == 0);
  assert(E.numrows == 2);
  assert(E.row[0].size == 510);
  assert(E.row[0].chars[509] == 'a');
  assert(strcmp(E.row[1].chars, "end\r") == 0);
}

static void assert_original_rows(int cx, int cy)
{
  assert(E.numrows == 2);
  assert(E.cx == cx && E.cy == cy);
  assert(E.row[0].size == 5 && strcmp(E.row[0].chars, "first") == 0);
  assert(E.row[1].size == 6 && strcmp(E.row[1].chars, "second") == 0);
  assert(E.dirty == 0);
  assert(strstr(E.statusmsg, "memory") != NULL);
}

static void test_edit_allocation_failures(void)
{
  reset_editor();
  assert(editorInsertRow(0, "first", 5));
  assert(editorInsertRow(1, "second", 6));
  E.dirty = 0;
  E.cx = 2;

  for (int fail = 0; fail < 2; fail++) {
    allocations_before_failure = fail;
    editorInsertNewline();
    allocations_before_failure = -1;
    assert_original_rows(2, 0);
  }

  allocations_before_failure = 0;
  editorInsertChar('x');
  allocations_before_failure = -1;
  assert_original_rows(2, 0);

  E.cx = 0;
  E.cy = 1;
  allocations_before_failure = 0;
  editorDelChar();
  allocations_before_failure = -1;
  assert_original_rows(0, 1);

  E.cy = 2;
  for (int fail = 0; fail < 2; fail++) {
    allocations_before_failure = fail;
    editorInsertChar('x');
    allocations_before_failure = -1;
    assert_original_rows(0, 2);
    allocations_before_failure = fail;
    editorInsertNewline();
    allocations_before_failure = -1;
    assert_original_rows(0, 2);
  }

  editorInsertChar('x');
  assert(E.numrows == 3 && strcmp(E.row[2].chars, "x") == 0);

  struct abuf ab = ABUF_INIT;
  abAppend(&ab, "old", 3);
  allocations_before_failure = 0;
  abAppend(&ab, "new", 3);
  allocations_before_failure = -1;
  abAppend(&ab, "ignored", 7);
  assert(ab.failed && ab.len == 3 && memcmp(ab.b, "old", 3) == 0);
  abFree(&ab);
}

static void test_load_allocation_failures(const char *filename)
{
  test_long_line(filename, -1, false);
  reset_editor();
  for (int fail = 0; fail < 6; fail++) {
    allocations_before_failure = fail;
    assert(editor_open(filename) == 1);
    allocations_before_failure = -1;
    assert(E.row == NULL && E.numrows == 0);
    FILE *fp = fopen(filename, "r");
    assert(fp);
    for (int i = 0; i < 1024; i++) assert(fgetc(fp) == 'a');
    assert(fgetc(fp) == '\n');
    assert(fclose(fp) == 0);
  }
  char missing[512];
  snprintf(missing, sizeof(missing), "%s.missing", filename);
  for (int fail = 0; fail < 2; fail++) {
    allocations_before_failure = fail;
    assert(editor_open(missing) == 1);
    allocations_before_failure = -1;
    assert(E.row == NULL && E.numrows == 0);
    assert(access(missing, F_OK) == -1 && errno == ENOENT);
  }
}

static void test_delete_at_end_of_buffer(const char *filename)
{
  reset_editor();
  snprintf(E.filename, sizeof(E.filename), "%s", filename);
  assert(editorInsertRow(0, "ab", 2));
  E.dirty = 0;

  int fds[2];
  assert(pipe(fds) == 0);
  int saved_stdin = dup(STDIN_FILENO);
  assert(saved_stdin >= 0);
  assert(dup2(fds[0], STDIN_FILENO) >= 0);
  close(fds[0]);
  setvbuf(stdin, NULL, _IONBF, 0);

  /* Delete at end of last row must be a no-op and keep cursor in bounds. */
  E.cy = 0;
  E.cx = 2;
  assert(write(fds[1], "\x1b[3~", 4) == 4);
  assert(editorProcessKeypress() == 0);
  assert(E.cy == 0 && E.cx == 2);
  assert(E.numrows == 1 && strcmp(E.row[0].chars, "ab") == 0);
  assert(E.dirty == 0);

  /* Delete inside a row deletes the character under the cursor. */
  E.cx = 0;
  assert(write(fds[1], "\x1b[3~", 4) == 4);
  assert(editorProcessKeypress() == 0);
  assert(E.cy == 0 && E.cx == 0);
  assert(E.numrows == 1 && strcmp(E.row[0].chars, "b") == 0);
  assert(E.dirty != 0);
  assert(editorSave() == 0);
  assert(E.dirty == 0);

  close(fds[1]);
  assert(dup2(saved_stdin, STDIN_FILENO) >= 0);
  close(saved_stdin);
  clearerr(stdin);
}

int main(void)
{
  char filename[] = "/tmp/editor-test-XXXXXX";
  int fd = mkstemp(filename);
  assert(fd >= 0);
  close(fd);
  test_long_filename();
  test_long_line(filename, -1, false);
  test_long_line(filename, 510, false);
  test_long_line(filename, 1021, true);
  test_split_crlf(filename);
  test_edit_allocation_failures();
  test_load_allocation_failures(filename);
  test_delete_at_end_of_buffer(filename);
  reset_editor();
  assert(unlink(filename) == 0);
  puts("Editor tests passed");
  return 0;
}
