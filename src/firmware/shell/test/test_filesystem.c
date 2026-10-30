/* Run shell commands against the SDK's FatFS on a RAM disk. */
#define _DEFAULT_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "ff.h"
#include "diskio.h"
#include "path_util.h"

#define SECTORS 8192
static unsigned char disk[SECTORS][512];
PARTITION VolToPart[FF_VOLUMES] = {{0, 0}};

DSTATUS disk_initialize(BYTE drive) { (void)drive; return 0; }
DSTATUS disk_status(BYTE drive) { (void)drive; return 0; }
DRESULT disk_read(BYTE drive, BYTE *out, LBA_t sector, UINT count)
{
  (void)drive;
  assert(sector + count <= SECTORS);
  memcpy(out, disk[sector], count * 512);
  return RES_OK;
}
DRESULT disk_write(BYTE drive, const BYTE *in, LBA_t sector, UINT count)
{
  (void)drive;
  assert(sector + count <= SECTORS);
  memcpy(disk[sector], in, count * 512);
  return RES_OK;
}
DRESULT disk_ioctl(BYTE drive, BYTE cmd, void *arg)
{
  (void)drive;
  if (cmd == GET_SECTOR_COUNT) *(LBA_t *)arg = SECTORS;
  else if (cmd == GET_BLOCK_SIZE) *(DWORD *)arg = 1;
  else if (cmd == GET_SECTOR_SIZE) *(WORD *)arg = 512;
  else if (cmd != CTRL_SYNC) return RES_PARERR;
  return RES_OK;
}
DWORD get_fattime(void) { return (DWORD)(2026 - 1980) << 25 | 1 << 21 | 1 << 16; }
/* Match the SDK FreeRTOS FatFS allocator, which zeroes long-name work buffers. */
void *ff_memalloc(size_t size) { return calloc(1, size); }
void ff_memfree(void *ptr) { free(ptr); }
int ff_mutex_create(int volume) { (void)volume; return 1; }
void ff_mutex_delete(int volume) { (void)volume; }
int ff_mutex_take(int volume) { (void)volume; return 1; }
void ff_mutex_give(int volume) { (void)volume; }

static FILE *fat_fopen(const char *path, const char *mode)
{
  FIL *file = malloc(sizeof(*file));
  assert(file);
  FRESULT result = f_open(file, path + strlen("/sdcard"),
                         mode[0] == 'r' ? FA_READ : FA_WRITE | FA_CREATE_ALWAYS);
  if (result != FR_OK) {
    free(file);
    errno = EACCES;
    return NULL;
  }
  return (FILE *)file;
}
static size_t fat_fread(void *buf, size_t size, size_t count, FILE *stream)
{
  UINT read = 0;
  f_read((FIL *)stream, buf, size * count, &read);
  return read / size;
}
static size_t fat_fwrite(const void *buf, size_t size, size_t count, FILE *stream)
{
  UINT written = 0;
  f_write((FIL *)stream, buf, size * count, &written);
  return written / size;
}
static int fat_fclose(FILE *stream)
{
  FRESULT result = f_close((FIL *)stream);
  free(stream);
  return result == FR_OK ? 0 : EOF;
}
static int fat_ferror(FILE *stream) { return f_error((FIL *)stream); }
static int fat_stat(const char *path, struct stat *st)
{
  memset(st, 0, sizeof(*st));
  if (strcmp(path, "/sdcard") == 0) {
    st->st_mode = S_IFDIR;
    return 0;
  }
  FILINFO info;
  if (f_stat(path + strlen("/sdcard"), &info) != FR_OK) return -1;
  st->st_mode = info.fattrib & AM_DIR ? S_IFDIR : S_IFREG;
  return 0;
}

static bool resolve_arg(const char *cmd, const char *in, char *out, size_t size);
#define fopen fat_fopen
#define fread fat_fread
#define fwrite fat_fwrite
#define fclose fat_fclose
#define ferror fat_ferror
#define stat(path, st) fat_stat(path, st)
#include "shell_commands.inc"
#undef fopen
#undef fread
#undef fwrite
#undef fclose
#undef ferror
#undef stat

static bool resolve_arg(const char *cmd, const char *in, char *out, size_t size)
{
  (void)cmd;
  return path_resolve(s_cwd, in, out, size);
}

static unsigned char contents[4096];
static void assert_contents(const char *path)
{
  FIL file;
  UINT read;
  unsigned char buffer[sizeof(contents)];
  assert(f_open(&file, path, FA_READ) == FR_OK);
  assert(f_size(&file) == sizeof(contents));
  assert(f_read(&file, buffer, sizeof(buffer), &read) == FR_OK);
  assert(read == sizeof(contents) && memcmp(buffer, contents, read) == 0);
  assert(f_close(&file) == FR_OK);
}

int main(void)
{
  FATFS fs;
  unsigned char work[4096];
  MKFS_PARM format = {.fmt = FM_FAT | FM_SFD, .au_size = 512};
  assert(f_mkfs("", &format, work, sizeof(work)) == FR_OK);
  assert(f_mount(&fs, "", 1) == FR_OK);
  for (size_t i = 0; i < sizeof(contents); i++) contents[i] = (unsigned char)i;
  FIL file;
  UINT written;
  assert(f_open(&file, "long source filename.txt", FA_WRITE | FA_CREATE_ALWAYS) == FR_OK);
  assert(f_write(&file, contents, sizeof(contents), &written) == FR_OK);
  assert(written == sizeof(contents));
  assert(f_close(&file) == FR_OK);

  FILINFO info;
  assert(f_stat("long source filename.txt", &info) == FR_OK);
  assert(info.altname[0] != '\0');
  char alias[64];
  snprintf(alias, sizeof(alias), "/sdcard/%s", info.altname);
  const char *aliases[] = {"/sdcard/long source filename.txt",
                           "/sdcard/LONG SOURCE FILENAME.TXT", alias};
  for (size_t i = 0; i < sizeof(aliases) / sizeof(aliases[0]); i++) {
    char *args[] = {"cp", "/sdcard/long source filename.txt", (char *)aliases[i]};
    assert(cmd_cp(3, args) == 1);
    assert_contents("long source filename.txt");
  }
  char *copy[] = {"cp", "/sdcard/long source filename.txt", "/sdcard/copy.txt"};
  assert(cmd_cp(3, copy) == 0);
  assert_contents("copy.txt");
  assert(f_rename("copy.txt", "long source filename.txt") == FR_EXIST);
  assert_contents("copy.txt");
  assert_contents("long source filename.txt");
  char *directory[] = {"cp", "/sdcard/long source filename.txt", "/sdcard"};
  assert(cmd_cp(3, directory) == 1);
  assert_contents("long source filename.txt");

  strcpy(s_cwd, "/somewhere");
  char *cd[] = {"cd"};
  assert(cmd_cd(1, cd) == 0);
  assert(strcmp(s_cwd, SHELL_HOME) == 0);

  FF_DIR directories[6];
  char path[32] = "";
  for (int depth = 0; depth < 6; depth++) {
    strcat(path, "/d");
    assert(f_mkdir(path) == FR_OK);
    assert(f_opendir(&directories[depth], path) == FR_OK);
  }
  for (int depth = 5; depth >= 0; depth--)
    assert(f_closedir(&directories[depth]) == FR_OK);
  assert(f_mount(NULL, "", 0) == FR_OK);
  puts("Filesystem regression tests passed");
  return 0;
}
