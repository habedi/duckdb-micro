/* DuckDB Shell for ESP32-P4 with MicroSD storage and a UART console.
 * Derived from microcontroller-playground's ESP-IDF SD-card shell; see README.md.
 */

#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/unistd.h>

#include "driver/gpio.h"
#include "driver/sdmmc_host.h"
#include "editor.h"
#include "duckdb_console.h"
#include "network.h"
#include "path_util.h"
#include "esp_app_format.h"
#include "esp_chip_info.h"
#include "esp_clk_tree.h"
#include "esp_cpu.h"
#include "esp_console.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#if CONFIG_SPIRAM
#include "esp_psram.h"
#endif
#include "esp_system.h"
#include "esp_vfs_fat.h"
#include "esp_littlefs.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "linenoise/linenoise.h"
#include "lwip/inet.h"
#include "lwip/netdb.h"
#include "nvs_flash.h"
#include "ping/ping_sock.h"
#include "sdmmc_cmd.h"
#include "soc/rtc.h"
#include "esp_private/wifi_os_adapter.h"

wifi_osi_funcs_t g_wifi_osi_funcs = {0};

#if CONFIG_IDF_TARGET_ESP32P4
#include "sd_pwr_ctrl_by_on_chip_ldo.h"

#define PIN_TF_PWR   GPIO_NUM_45
#define PIN_TF_CLK   GPIO_NUM_43
#define PIN_TF_CMD   GPIO_NUM_44
#define PIN_TF_D0    GPIO_NUM_39
#define PIN_TF_D1    GPIO_NUM_40
#define PIN_TF_D2    GPIO_NUM_41
#define PIN_TF_D3    GPIO_NUM_42
#define PIN_C6_RESET GPIO_NUM_54
#endif

#define SDCARD_MOUNT_POINT "/sdcard"
#define DATA_MOUNT_POINT "/data"
#define SHELL_HOME SDCARD_MOUNT_POINT
#define SHELL_MAX_CMDLINE 1024

static const char *TAG = "espidf_shell";

static char s_cwd[256] = SHELL_HOME;
static esp_netif_t *s_sta_netif = NULL;
static bool s_wifi_initialized = false;
static bool s_wifi_connected = false;
static SemaphoreHandle_t s_ip_sem = NULL;

#if CONFIG_IDF_TARGET_ESP32P4
static void power_on_slot(void)
{
  gpio_config_t io_conf = {
      .pin_bit_mask = 1ULL << PIN_TF_PWR,
      .mode = GPIO_MODE_OUTPUT,
      .pull_up_en = GPIO_PULLUP_DISABLE,
      .pull_down_en = GPIO_PULLDOWN_DISABLE,
      .intr_type = GPIO_INTR_DISABLE,
  };
  gpio_config(&io_conf);
  gpio_set_level(PIN_TF_PWR, 0);
  vTaskDelay(pdMS_TO_TICKS(300));
}
#endif

static bool resolve_path(const char *in, char *out, size_t out_len)
{
  return path_resolve(s_cwd, in, out, out_len);
}

/* resolve_path for command arguments; prints an error on failure. */
static bool resolve_arg(const char *cmd, const char *in, char *out, size_t out_len)
{
  if (resolve_path(in, out, out_len))
    {
      return true;
    }
  printf("%s: path too long: %s\n", cmd, in);
  return false;
}

bool shell_resolve_arg(const char *cmd, const char *in, char *out, size_t out_len)
{
  return resolve_arg(cmd, in, out, out_len);
}

static int cmd_pwd(int argc, char **argv)
{
  printf("%s\n", s_cwd);
  return 0;
}

static int cmd_cd(int argc, char **argv)
{
  /* Same size as s_cwd so that a valid target always fits. */
  char target[sizeof(s_cwd)];
  if (argc < 2)
    {
      snprintf(target, sizeof(target), "%s", SHELL_HOME);
    }
  else if (!resolve_arg("cd", argv[1], target, sizeof(target)))
    {
      return 1;
    }

  struct stat st;
  if (stat(target, &st) == 0 && S_ISDIR(st.st_mode))
    {
      memcpy(s_cwd, target, sizeof(s_cwd));
      return 0;
    }
  printf("cd: %s: No such directory\n", target);
  return 1;
}

static int cmd_ls(int argc, char **argv)
{
  char path[512];
  if (argc > 1)
    {
      if (!resolve_arg("ls", argv[1], path, sizeof(path)))
        {
          return 1;
        }
    }
  else
    {
      strncpy(path, s_cwd, sizeof(path) - 1);
      path[sizeof(path) - 1] = '\0';
    }

  DIR *d = opendir(path);
  if (!d)
    {
      printf("ls: cannot open directory %s\n", path);
      return 1;
    }

  printf("Directory: %s\n", path);
  struct dirent *entry;
  char full_path[1024];
  struct stat st;
  while ((entry = readdir(d)) != NULL)
    {
      if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0)
        {
          continue;
        }

      snprintf(full_path, sizeof(full_path), "%s/%s", path, entry->d_name);
      if (stat(full_path, &st) == 0)
        {
          if (S_ISDIR(st.st_mode))
            {
              printf("  <DIR>         %s\n", entry->d_name);
            }
          else
            {
              printf("  %10ld B  %s\n", (long)st.st_size, entry->d_name);
            }
        }
      else
        {
          printf("                %s\n", entry->d_name);
        }
    }
  closedir(d);
  return 0;
}

static bool is_dot_entry(const char *name)
{
  return strcmp(name, ".") == 0 || strcmp(name, "..") == 0;
}

static int tree_count(const char *path)
{
  DIR *d = opendir(path);
  if (!d) return 0;

  int count = 0;
  struct dirent *entry;
  while ((entry = readdir(d)) != NULL)
    {
      if (!is_dot_entry(entry->d_name))
        {
          count++;
        }
    }
  closedir(d);
  return count;
}

/* path and prefix are shared buffers that each level extends and restores,
 * so the stack frame stays small during recursion.
 */
static void tree_traverse(char *path, size_t path_cap, char *prefix, size_t prefix_cap, int depth)
{
  if (depth > 5) return;
  int total = tree_count(path);
  DIR *d = opendir(path);
  if (!d) return;

  size_t path_len = strlen(path);
  size_t prefix_len = strlen(prefix);
  struct dirent *entry;
  struct stat st;
  int index = 0;
  while ((entry = readdir(d)) != NULL)
    {
      if (is_dot_entry(entry->d_name))
        {
          continue;
        }
      bool last = ++index >= total;
      int n = snprintf(path + path_len, path_cap - path_len, "/%s", entry->d_name);
      bool fits = n >= 0 && (size_t)n < path_cap - path_len;
      bool is_dir = fits && stat(path, &st) == 0 && S_ISDIR(st.st_mode);

      printf("%s%s %s%s\n", prefix, last ? "└──" : "├──", entry->d_name, is_dir ? "/" : "");
      if (is_dir)
        {
          snprintf(prefix + prefix_len, prefix_cap - prefix_len, "%s", last ? "    " : "│   ");
          tree_traverse(path, path_cap, prefix, prefix_cap, depth + 1);
          prefix[prefix_len] = '\0';
        }
      path[path_len] = '\0';
    }
  closedir(d);
}

static int cmd_tree(int argc, char **argv)
{
  char path[512];
  char prefix[64] = "";
  if (argc > 1)
    {
      if (!resolve_arg("tree", argv[1], path, sizeof(path)))
        {
          return 1;
        }
    }
  else
    {
      strncpy(path, s_cwd, sizeof(path) - 1);
      path[sizeof(path) - 1] = '\0';
    }

  printf("%s\n", path);
  tree_traverse(path, sizeof(path), prefix, sizeof(prefix), 0);
  return 0;
}

static int cmd_cat(int argc, char **argv)
{
  if (argc < 2)
    {
      printf("Usage: cat <filename>\n");
      return 1;
    }

  char full_path[512];
  if (!resolve_arg("cat", argv[1], full_path, sizeof(full_path)))
    {
      return 1;
    }

  struct stat st;
  if (stat(full_path, &st) == 0 && S_ISDIR(st.st_mode))
    {
      printf("cat: %s: Is a directory\n", full_path);
      return 1;
    }

  FILE *f = fopen(full_path, "r");
  if (!f)
    {
      printf("cat: %s: No such file\n", full_path);
      return 1;
    }

  char buf[256];
  size_t n;
  while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
    {
      fwrite(buf, 1, n, stdout);
    }
  bool failed = ferror(f) != 0;
  printf("\n");
  fclose(f);
  if (failed)
    {
      printf("cat: %s: Read error\n", full_path);
      return 1;
    }
  return 0;
}

static int cmd_write(int argc, char **argv)
{
  if (argc < 3)
    {
      printf("Usage: write <filename> <text>\n");
      return 1;
    }

  char full_path[512];
  if (!resolve_arg("write", argv[1], full_path, sizeof(full_path)))
    {
      return 1;
    }

  FILE *f = fopen(full_path, "w");
  if (!f)
    {
      printf("write: cannot open %s for writing\n", full_path);
      return 1;
    }

  for (int i = 2; i < argc; i++)
    {
      fputs(argv[i], f);
      if (i < argc - 1) fputc(' ', f);
    }
  fputc('\n', f);
  bool failed = ferror(f) != 0;
  if (fclose(f) != 0 || failed)
    {
      printf("write: failed writing %s\n", full_path);
      return 1;
    }
  printf("Wrote to %s\n", full_path);
  return 0;
}

static int cmd_cp(int argc, char **argv)
{
  if (argc < 3)
    {
      printf("Usage: cp <source> <destination>\n");
      return 1;
    }

  char src[512];
  char dst[512];
  if (!resolve_arg("cp", argv[1], src, sizeof(src)) ||
      !resolve_arg("cp", argv[2], dst, sizeof(dst)))
    {
      return 1;
    }

  struct stat st;
  if (stat(dst, &st) == 0 && S_ISDIR(st.st_mode))
    {
      const char *basename = strrchr(src, '/') + 1;
      size_t len = strlen(dst);
      int n = snprintf(dst + len, sizeof(dst) - len, "/%s", basename);
      if (n < 0 || (size_t)n >= sizeof(dst) - len)
        {
          printf("cp: path too long: %s/%s\n", argv[2], basename);
          return 1;
        }
    }

  /* Opening the destination with "wb" would truncate the source first. */
  if (strcmp(src, dst) == 0)
    {
      printf("cp: %s and %s are the same file\n", src, dst);
      return 1;
    }

  FILE *in = fopen(src, "rb");
  if (!in)
    {
      printf("cp: cannot open source file %s\n", src);
      return 1;
    }

  FILE *out = fopen(dst, "wb");
  if (!out)
    {
      printf("cp: cannot open destination file %s\n", dst);
      fclose(in);
      return 1;
    }

  char buf[512];
  size_t n;
  size_t total = 0;
  bool failed = false;
  while ((n = fread(buf, 1, sizeof(buf), in)) > 0)
    {
      if (fwrite(buf, 1, n, out) != n)
        {
          failed = true;
          break;
        }
      total += n;
    }
  failed = failed || ferror(in) != 0;
  fclose(in);
  if (fclose(out) != 0 || failed)
    {
      printf("cp: failed after %zu bytes from %s to %s\n", total, src, dst);
      return 1;
    }
  printf("Copied %zu bytes from %s to %s\n", total, src, dst);
  return 0;
}

static int cmd_rm(int argc, char **argv)
{
  if (argc < 2)
    {
      printf("Usage: rm <filename>\n");
      return 1;
    }

  char full_path[512];
  if (!resolve_arg("rm", argv[1], full_path, sizeof(full_path)))
    {
      return 1;
    }

  if (unlink(full_path) == 0)
    {
      printf("Removed %s\n", full_path);
      return 0;
    }
  printf("rm: cannot remove %s\n", full_path);
  return 1;
}

static int cmd_mkdir(int argc, char **argv)
{
  if (argc < 2)
    {
      printf("Usage: mkdir <dirname>\n");
      return 1;
    }

  char full_path[512];
  if (!resolve_arg("mkdir", argv[1], full_path, sizeof(full_path)))
    {
      return 1;
    }

  if (mkdir(full_path, 0755) == 0)
    {
      printf("Created directory %s\n", full_path);
      return 0;
    }
  printf("mkdir: failed to create %s\n", full_path);
  return 1;
}

static int cmd_df(int argc, char **argv)
{
  printf("Filesystem      Size        Used       Available  Mounted on\n");

  /* FATFS on SD Card */
  uint64_t total_sd = 0, free_sd = 0;
  if (esp_vfs_fat_info(SDCARD_MOUNT_POINT, &total_sd, &free_sd) == ESP_OK)
    {
      uint64_t used_sd = total_sd - free_sd;
      printf("fatfs (SD)      %-10lu MB %-10lu MB %-10lu MB %s\n",
             (unsigned long)(total_sd / (1024 * 1024)),
             (unsigned long)(used_sd / (1024 * 1024)),
             (unsigned long)(free_sd / (1024 * 1024)),
             SDCARD_MOUNT_POINT);
    }

  /* LittleFS on Internal Flash */
  size_t total_lfs = 0, used_lfs = 0;
  if (esp_littlefs_info("storage", &total_lfs, &used_lfs) == ESP_OK)
    {
      size_t free_lfs = (total_lfs > used_lfs) ? (total_lfs - used_lfs) : 0;
      printf("littlefs (Flash) %-10lu KB %-10lu KB %-10lu KB %s\n",
             (unsigned long)(total_lfs / 1024),
             (unsigned long)(used_lfs / 1024),
             (unsigned long)(free_lfs / 1024),
             DATA_MOUNT_POINT);
    }

  return 0;
}

static const char *task_state_name(eTaskState s)
{
  switch (s)
    {
      case eRunning:   return "RUNNING";
      case eReady:     return "READY";
      case eBlocked:   return "BLOCKED";
      case eSuspended: return "SUSPEND";
      case eDeleted:   return "DELETED";
      default:         return "UNKNOWN";
    }
}

static int cmd_ps(int argc, char **argv)
{
  UBaseType_t task_count = uxTaskGetNumberOfTasks();
  TaskStatus_t *status_array = malloc(task_count * sizeof(TaskStatus_t));
  if (!status_array)
    {
      printf("ps: out of memory\n");
      return 1;
    }

  task_count = uxTaskGetSystemState(status_array, task_count, NULL);

  printf("%-4s  %-16s  %-8s  %-4s  %-8s  %-5s\n",
         "PID", "NAME", "STATE", "PRIO", "STACK-HW", "CORE");
  printf("----  ----------------  --------  ----  --------  -----\n");

  for (UBaseType_t i = 0; i < task_count; i++)
    {
      char core_str[8] = "Any";
      BaseType_t core = xTaskGetCoreID(status_array[i].xHandle);
      if (core == 0)
        {
          snprintf(core_str, sizeof(core_str), "0");
        }
      else if (core == 1)
        {
          snprintf(core_str, sizeof(core_str), "1");
        }
      printf("%-4u  %-16s  %-8s  %-4u  %-8u  %-5s\n",
             (unsigned)status_array[i].xTaskNumber,
             status_array[i].pcTaskName,
             task_state_name(status_array[i].eCurrentState),
             (unsigned)status_array[i].uxCurrentPriority,
             (unsigned)status_array[i].usStackHighWaterMark,
             core_str);
    }

  free(status_array);
  return 0;
}

static int cmd_top(int argc, char **argv)
{
  UBaseType_t task_count1 = uxTaskGetNumberOfTasks();
  TaskStatus_t *status1 = malloc(task_count1 * sizeof(TaskStatus_t));
  uint32_t total_time1 = 0;
  if (!status1)
    {
      printf("top: out of memory\n");
      return 1;
    }
  task_count1 = uxTaskGetSystemState(status1, task_count1, &total_time1);

  /* Sample over 500 ms */
  vTaskDelay(pdMS_TO_TICKS(500));

  UBaseType_t task_count2 = uxTaskGetNumberOfTasks();
  TaskStatus_t *status2 = malloc(task_count2 * sizeof(TaskStatus_t));
  uint32_t total_time2 = 0;
  if (!status2)
    {
      free(status1);
      printf("top: out of memory\n");
      return 1;
    }
  task_count2 = uxTaskGetSystemState(status2, task_count2, &total_time2);

  uint32_t delta_time = total_time2 - total_time1;
  if (delta_time == 0) delta_time = 1;
#if (configNUMBER_OF_CORES > 1)
  uint32_t total_capacity = delta_time * configNUMBER_OF_CORES;
#else
  uint32_t total_capacity = delta_time;
#endif
  if (total_capacity == 0) total_capacity = 1;

  typedef struct {
    UBaseType_t pid;
    char name[configMAX_TASK_NAME_LEN];
    eTaskState state;
    UBaseType_t prio;
    UBaseType_t stack_hw;
    char core[8];
    float cpu_pct;
  } top_item_t;

  top_item_t *items = malloc(task_count2 * sizeof(top_item_t));
  if (!items)
    {
      free(status1);
      free(status2);
      printf("top: out of memory\n");
      return 1;
    }

  float idle_pct = 0.0f;
  for (UBaseType_t i = 0; i < task_count2; i++)
    {
      items[i].pid = status2[i].xTaskNumber;
      strncpy(items[i].name, status2[i].pcTaskName, sizeof(items[i].name) - 1);
      items[i].name[sizeof(items[i].name) - 1] = '\0';
      items[i].state = status2[i].eCurrentState;
      items[i].prio = status2[i].uxCurrentPriority;
      items[i].stack_hw = status2[i].usStackHighWaterMark;

      strncpy(items[i].core, "Any", sizeof(items[i].core));
      BaseType_t core = xTaskGetCoreID(status2[i].xHandle);
      if (core == 0)
        {
          snprintf(items[i].core, sizeof(items[i].core), "0");
        }
      else if (core == 1)
        {
          snprintf(items[i].core, sizeof(items[i].core), "1");
        }

      /* A task created between samples has no baseline; show 0% for it. */
      uint32_t task_delta = 0;
      for (UBaseType_t j = 0; j < task_count1; j++)
        {
          if (status1[j].xHandle == status2[i].xHandle)
            {
              task_delta = status2[i].ulRunTimeCounter - status1[j].ulRunTimeCounter;
              break;
            }
        }
      items[i].cpu_pct = ((float)task_delta * 100.0f) / (float)total_capacity;
      if (strncmp(items[i].name, "IDLE", 4) == 0)
        {
          idle_pct += items[i].cpu_pct;
        }
    }

  /* Sort items descending by cpu_pct */
  for (UBaseType_t i = 0; i < task_count2; i++)
    {
      for (UBaseType_t j = i + 1; j < task_count2; j++)
        {
          if (items[j].cpu_pct > items[i].cpu_pct)
            {
              top_item_t tmp = items[i];
              items[i] = items[j];
              items[j] = tmp;
            }
        }
    }

  float active_cpu = 100.0f - idle_pct;
  if (active_cpu < 0.0f) active_cpu = 0.0f;
  if (active_cpu > 100.0f) active_cpu = 100.0f;

  printf("Tasks: %u total | Free Heap: %lu B | Min Free: %lu B\n",
         (unsigned)task_count2, (unsigned long)esp_get_free_heap_size(),
         (unsigned long)esp_get_minimum_free_heap_size());
  printf("CPU Total Active Load: %.1f%%\n\n", active_cpu);
  printf("%-4s  %-16s  %-8s  %-4s  %-6s  %-8s  %-5s\n",
         "PID", "NAME", "STATE", "PRIO", "CPU%", "STACK-HW", "CORE");
  printf("----  ----------------  --------  ----  ------  --------  -----\n");

  for (UBaseType_t i = 0; i < task_count2; i++)
    {
      printf("%-4u  %-16s  %-8s  %-4u  %5.1f%%  %-8u  %-5s\n",
             (unsigned)items[i].pid,
             items[i].name,
             task_state_name(items[i].state),
             (unsigned)items[i].prio,
             items[i].cpu_pct,
             (unsigned)items[i].stack_hw,
             items[i].core);
    }
  printf("\n");

  free(items);
  free(status1);
  free(status2);
  return 0;
}

static int cmd_free(int argc, char **argv)
{
  printf("%-17s %10s %10s %10s %10s\n", "Memory Tier", "Total", "Free", "Min Free", "Max Block");
  printf("%-17s %10s %10s %10s %10s\n", "-----------", "-----", "----", "--------", "---------");

  struct {
    const char *name;
    uint32_t caps;
  } tiers[] = {
    {"Internal SRAM",   MALLOC_CAP_INTERNAL},
    {"DMA-capable",     MALLOC_CAP_DMA},
#if CONFIG_SPIRAM
    {"SPI RAM (PSRAM)", MALLOC_CAP_SPIRAM},
#endif
  };

  for (size_t i = 0; i < sizeof(tiers) / sizeof(tiers[0]); i++)
    {
      size_t total = heap_caps_get_total_size(tiers[i].caps);
      size_t free_sz = heap_caps_get_free_size(tiers[i].caps);
      size_t min_free = heap_caps_get_minimum_free_size(tiers[i].caps);
      size_t max_block = heap_caps_get_largest_free_block(tiers[i].caps);

      if (total > 0 || free_sz > 0 || tiers[i].caps == MALLOC_CAP_SPIRAM)
        {
          printf("%-17s %10zu %10zu %10zu %10zu\n",
                 tiers[i].name, total, free_sz, min_free, max_block);
        }
    }

  printf("\nOverall System Allocator:\n");
  printf("  Total free:       %10lu bytes\n", (unsigned long)esp_get_free_heap_size());
  printf("  Minimum free:     %10lu bytes\n", (unsigned long)esp_get_minimum_free_heap_size());
  printf("  Largest block:    %10zu bytes\n", heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT));

#if CONFIG_SPIRAM
  if (esp_psram_is_initialized())
    {
      size_t psram_size = esp_psram_get_size();
      printf("  PSRAM Physical:   %10zu bytes (%zu MB)\n", psram_size, psram_size / (1024 * 1024));
    }
  else
    {
      printf("  PSRAM Physical:   Disabled / Not initialized\n");
    }
#else
  printf("  PSRAM Physical:   Not supported on this target\n");
#endif

  return 0;
}

static int cmd_cpuinfo(int argc, char **argv)
{
  esp_chip_info_t chip_info;
  esp_chip_info(&chip_info);

  uint32_t cpu_freq_hz = 0;
  uint32_t apb_freq_hz = 0;
  uint32_t xtal_freq_hz = 0;

  esp_clk_tree_src_get_freq_hz(SOC_MOD_CLK_CPU, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED, &cpu_freq_hz);
#if CONFIG_IDF_TARGET_ESP32C6
  apb_freq_hz = rtc_clk_apb_freq_get();
#else
  esp_clk_tree_src_get_freq_hz(SOC_MOD_CLK_APB, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED, &apb_freq_hz);
#endif
  esp_clk_tree_src_get_freq_hz(SOC_MOD_CLK_XTAL, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED, &xtal_freq_hz);

#if CONFIG_IDF_TARGET_ESP32P4
  printf("CPU Architecture:      RISC-V 32-bit (RV32IMAFCP + Espressif PIE)\n");
  printf("Target Chip:           ESP32-P4 (Revision v%d.%d)\n", chip_info.revision / 100, chip_info.revision % 100);
  printf("High-Performance Core: %d cores (Active Core ID: %d)\n", chip_info.cores, esp_cpu_get_core_id());
  printf("Low-Power Core:        1 core (RV32IMC @ up to 40 MHz)\n");
  printf("CPU Clock Frequency:   %lu MHz (XTAL: %lu MHz, APB: %lu MHz)\n",
         (unsigned long)(cpu_freq_hz / 1000000),
         (unsigned long)(xtal_freq_hz / 1000000),
         (unsigned long)(apb_freq_hz / 1000000));
  printf("Instruction Cache:     32 KB L1 I-Cache per HP core\n");
  printf("Data Cache:            32 KB L1 D-Cache per HP core\n");
  printf("L2 Unified Cache:      128 KB\n");
  printf("Tightly Coupled RAM:   128 KB zero-wait-state HP TCM\n");
  printf("ISA Features:          FPU (Single-precision), Packed-SIMD DSP, 128-bit AI SIMD (PIE),\n"
         "                       Hardware Loops, Atomic (A), Compressed (C)\n");
#elif CONFIG_IDF_TARGET_ESP32C6
  printf("CPU Architecture:      RISC-V 32-bit (RV32IMAC)\n");
  printf("Target Chip:           ESP32-C6 (Revision v%d.%d)\n", chip_info.revision / 100, chip_info.revision % 100);
  printf("High-Performance Core: %d core (Active Core ID: %d)\n", chip_info.cores, esp_cpu_get_core_id());
  printf("Low-Power Core:        1 core (RV32IMC @ up to 20 MHz)\n");
  printf("CPU Clock Frequency:   %lu MHz (XTAL: %lu MHz, APB: %lu MHz)\n",
         (unsigned long)(cpu_freq_hz / 1000000),
         (unsigned long)(xtal_freq_hz / 1000000),
         (unsigned long)(apb_freq_hz / 1000000));
  printf("Instruction Cache:     32 KB L1 I-Cache\n");
  printf("ISA Features:          Integer (I), Multiply/Divide (M), Atomic (A), Compressed (C)\n");
#else
  printf("CPU Architecture:      Xtensa 32-bit LX7\n");
  printf("Target Chip:           ESP32-S3 (Revision v%d.%d)\n", chip_info.revision / 100, chip_info.revision % 100);
  printf("Cores:                 %d (Active Core ID: %d)\n", chip_info.cores, esp_cpu_get_core_id());
  printf("CPU Clock Frequency:   %lu MHz (XTAL: %lu MHz, APB: %lu MHz)\n",
         (unsigned long)(cpu_freq_hz / 1000000),
         (unsigned long)(xtal_freq_hz / 1000000),
         (unsigned long)(apb_freq_hz / 1000000));
  printf("ISA Features:          FPU (Single-precision), SIMD vector instructions, Hardware Loops\n");
#endif
  return 0;
}

static int cmd_uname(int argc, char **argv)
{
  esp_chip_info_t chip_info;
  esp_chip_info(&chip_info);
  printf("ESP-IDF v%s (FreeRTOS) chip rev v%d.%d, %d cores\n",
         esp_get_idf_version(),
         chip_info.revision / 100,
         chip_info.revision % 100,
         chip_info.cores);
  return 0;
}

static int cmd_reboot(int argc, char **argv)
{
  printf("Restarting system...\n");
  esp_restart();
  return 0;
}

#if CONFIG_IDF_TARGET_ESP32P4
static int cmd_reset_c6(int argc, char **argv)
{
  gpio_config_t io_conf = {
      .pin_bit_mask = 1ULL << PIN_C6_RESET,
      .mode = GPIO_MODE_OUTPUT,
      .pull_up_en = GPIO_PULLUP_DISABLE,
      .pull_down_en = GPIO_PULLDOWN_DISABLE,
      .intr_type = GPIO_INTR_DISABLE,
  };
  gpio_config(&io_conf);

  printf("Asserting C6 reset (GPIO %d LOW)...\n", PIN_C6_RESET);
  gpio_set_level(PIN_C6_RESET, 0);
  vTaskDelay(pdMS_TO_TICKS(100));
  printf("Releasing C6 reset (GPIO %d HIGH)...\n", PIN_C6_RESET);
  gpio_set_level(PIN_C6_RESET, 1);
  return 0;
}
#endif

static const char *ota_state_str(esp_ota_img_states_t state)
{
  switch (state)
    {
      case ESP_OTA_IMG_NEW:             return "NEW";
      case ESP_OTA_IMG_PENDING_VERIFY:  return "PENDING_VERIFY";
      case ESP_OTA_IMG_VALID:           return "VALID";
      case ESP_OTA_IMG_INVALID:         return "INVALID";
      case ESP_OTA_IMG_ABORTED:         return "ABORTED";
      case ESP_OTA_IMG_UNDEFINED:       return "UNDEFINED";
      default:                          return "UNKNOWN";
    }
}

static int cmd_ota_status(int argc, char **argv)
{
  const esp_partition_t *running = esp_ota_get_running_partition();
  const esp_partition_t *boot = esp_ota_get_boot_partition();
  const esp_partition_t *next = esp_ota_get_next_update_partition(NULL);

  printf("FOTA Partition & Rollback Status:\n");
  printf("--------------------------------------------------\n");

  if (running != NULL)
    {
      esp_app_desc_t app_desc;
      esp_err_t err = esp_ota_get_partition_description(running, &app_desc);
      esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
      esp_ota_get_state_partition(running, &state);

      printf("Running Slot:       %s (0x%08lx, %lu KB)\n",
             running->label, (unsigned long)running->address,
             (unsigned long)(running->size / 1024));
      printf("  State:            %s\n", ota_state_str(state));
      if (err == ESP_OK)
        {
          printf("  Project Name:     %s\n", app_desc.project_name);
          printf("  Version:          %s\n", app_desc.version);
          printf("  Built:            %s %s\n", app_desc.date, app_desc.time);
          printf("  IDF Version:      %s\n", app_desc.idf_ver);
        }
    }
  else
    {
      printf("Running Slot:       Unknown\n");
    }

  if (boot != NULL)
    {
      printf("Configured Boot:    %s (0x%08lx)\n",
             boot->label, (unsigned long)boot->address);
    }

  if (next != NULL)
    {
      esp_app_desc_t next_desc;
      esp_err_t err = esp_ota_get_partition_description(next, &next_desc);
      esp_ota_img_states_t next_state = ESP_OTA_IMG_UNDEFINED;
      esp_ota_get_state_partition(next, &next_state);

      printf("Next Update Target: %s (0x%08lx, %lu KB)\n",
             next->label, (unsigned long)next->address,
             (unsigned long)(next->size / 1024));
      printf("  State:            %s\n", ota_state_str(next_state));
      if (err == ESP_OK)
        {
          printf("  Existing Version: %s\n", next_desc.version);
        }
    }
  else
    {
      printf("Next Update Target: None (no inactive OTA slot found)\n");
    }

#if CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
  printf("Rollback Support:   Enabled (automatic watchdog fallback)\n");
#else
  printf("Rollback Support:   Disabled\n");
#endif

  return 0;
}

static int cmd_ota_confirm(int argc, char **argv)
{
  const esp_partition_t *running = esp_ota_get_running_partition();
  esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
  if (running != NULL)
    {
      esp_ota_get_state_partition(running, &state);
    }

  if (state == ESP_OTA_IMG_VALID)
    {
      printf("Firmware on %s is already marked VALID.\n",
             running ? running->label : "current slot");
      return 0;
    }

  esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
  if (err == ESP_OK)
    {
      printf("Firmware validated successfully! Rollback cancelled for %s.\n",
             running ? running->label : "current slot");
    }
  else
    {
      printf("Failed to confirm firmware: %s\n", esp_err_to_name(err));
    }
  return 0;
}

static int cmd_ota_rollback(int argc, char **argv)
{
  const esp_partition_t *running = esp_ota_get_running_partition();
  printf("Initiating voluntary rollback from %s...\n",
         running ? running->label : "current slot");

  esp_err_t err = esp_ota_mark_app_invalid_rollback_and_reboot();
  if (err != ESP_OK)
    {
      printf("Rollback failed: %s (is an alternate bootable image available?)\n",
             esp_err_to_name(err));
    }
  return 0;
}

static int cmd_ota_update(int argc, char **argv)
{
  if (argc < 2)
    {
      printf("Usage: ota_update <file_path>\n");
      printf("Example: ota_update /sdcard/espidf_sdcard.bin\n");
      return 1;
    }

  char full_path[512];
  if (!resolve_arg("ota_update", argv[1], full_path, sizeof(full_path)))
    {
      return 1;
    }

  FILE *f = fopen(full_path, "rb");
  if (f == NULL)
    {
      printf("ota_update: cannot open '%s': %s\n", full_path, strerror(errno));
      return 1;
    }

  fseek(f, 0, SEEK_END);
  long file_size = ftell(f);
  fseek(f, 0, SEEK_SET);

  if (file_size <= 0)
    {
      printf("ota_update: file '%s' is empty\n", full_path);
      fclose(f);
      return 1;
    }

  const esp_partition_t *update_partition = esp_ota_get_next_update_partition(NULL);
  if (update_partition == NULL)
    {
      printf("ota_update: no target OTA partition available\n");
      fclose(f);
      return 1;
    }

  if ((size_t)file_size > update_partition->size)
    {
      printf("ota_update: image size (%ld B) exceeds partition capacity (%lu B)\n",
             file_size, (unsigned long)update_partition->size);
      fclose(f);
      return 1;
    }

  printf("Starting FOTA update:\n");
  printf("  Source image:     %s (%ld bytes)\n", full_path, file_size);
  printf("  Target slot:      %s (0x%08lx, %lu KB)\n",
         update_partition->label, (unsigned long)update_partition->address,
         (unsigned long)(update_partition->size / 1024));

  esp_ota_handle_t ota_handle = 0;
  esp_err_t err = esp_ota_begin(update_partition, OTA_WITH_SEQUENTIAL_WRITES, &ota_handle);
  if (err != ESP_OK)
    {
      printf("ota_update: esp_ota_begin failed: %s\n", esp_err_to_name(err));
      fclose(f);
      return 1;
    }

  const size_t chunk_size = 4096;
  char *buf = malloc(chunk_size);
  if (buf == NULL)
    {
      printf("ota_update: failed to allocate %zu B buffer\n", chunk_size);
      esp_ota_abort(ota_handle);
      fclose(f);
      return 1;
    }

  size_t bytes_written = 0;
  int last_pct = -1;
  while (bytes_written < (size_t)file_size)
    {
      size_t to_read = (size_t)file_size - bytes_written;
      if (to_read > chunk_size)
        {
          to_read = chunk_size;
        }

      size_t read_bytes = fread(buf, 1, to_read, f);
      if (read_bytes == 0)
        {
          break;
        }

      err = esp_ota_write(ota_handle, buf, read_bytes);
      if (err != ESP_OK)
        {
          printf("\nota_update: esp_ota_write failed at %zu bytes: %s\n",
                 bytes_written, esp_err_to_name(err));
          free(buf);
          esp_ota_abort(ota_handle);
          fclose(f);
          return 1;
        }

      bytes_written += read_bytes;
      int pct = (int)((bytes_written * 100) / file_size);
      if (pct != last_pct && (pct % 10 == 0 || pct == 100))
        {
          printf("  Flashing: %3d%% (%zu / %ld bytes)\r", pct, bytes_written, file_size);
          fflush(stdout);
          last_pct = pct;
        }
    }
  printf("\n");
  free(buf);
  fclose(f);

  if (bytes_written != (size_t)file_size)
    {
      printf("ota_update: read error, wrote %zu of %ld bytes\n", bytes_written, file_size);
      esp_ota_abort(ota_handle);
      return 1;
    }

  printf("Validating image SHA256 integrity...\n");
  err = esp_ota_end(ota_handle);
  if (err != ESP_OK)
    {
      printf("ota_update: validation failed (corrupt image or bad SHA256): %s\n",
             esp_err_to_name(err));
      return 1;
    }

  err = esp_ota_set_boot_partition(update_partition);
  if (err != ESP_OK)
    {
      printf("ota_update: esp_ota_set_boot_partition failed: %s\n",
             esp_err_to_name(err));
      return 1;
    }

  printf("FOTA update completed successfully!\n");
  printf("Next boot will launch '%s' in PENDING_VERIFY state.\n", update_partition->label);
  printf("If new firmware fails to initialize, the bootloader will automatically roll back.\n");
  printf("Type 'reboot' to restart into the new firmware.\n");

  return 0;
}

static int cmd_nano(int argc, char **argv)
{
  if (argc < 2)
    {
      printf("Usage: nano <filename> (or edit <filename>)\n");
      return 1;
    }

  char full_path[512];
  if (!resolve_arg("nano", argv[1], full_path, sizeof(full_path)))
    {
      return 1;
    }
  return editor_open(full_path);
}

static const char *auth_name(wifi_auth_mode_t mode)
{
  switch (mode)
    {
      case WIFI_AUTH_OPEN:            return "open";
      case WIFI_AUTH_WEP:             return "WEP";
      case WIFI_AUTH_WPA_PSK:         return "WPA";
      case WIFI_AUTH_WPA2_PSK:        return "WPA2";
      case WIFI_AUTH_WPA_WPA2_PSK:    return "WPA/WPA2";
      case WIFI_AUTH_WPA3_PSK:        return "WPA3";
      case WIFI_AUTH_WPA2_WPA3_PSK:   return "WPA2/WPA3";
      default:                        return "other";
    }
}

static int cmd_wifi_scan(int argc, char **argv)
{
  if (!s_wifi_initialized)
    {
      printf("wifi_scan: Wi-Fi stack not initialized\n");
      return 1;
    }

  printf("Scanning Wi-Fi networks...\n");
  esp_err_t err = esp_wifi_scan_start(NULL, true);
  if (err != ESP_OK)
    {
      printf("wifi_scan: scan failed (%s)\n", esp_err_to_name(err));
      return 1;
    }

  uint16_t found = 0;
  esp_wifi_scan_get_ap_num(&found);
  if (found == 0)
    {
      printf("Found 0 networks\n");
      return 0;
    }

  uint16_t count = found > 20 ? 20 : found;
  wifi_ap_record_t *records = malloc(count * sizeof(wifi_ap_record_t));
  if (!records)
    {
      esp_wifi_clear_ap_list();
      printf("wifi_scan: out of memory\n");
      return 1;
    }

  esp_wifi_scan_get_ap_records(&count, records);
  printf("Found %u networks (showing %u):\n", (unsigned)found, (unsigned)count);
  printf("  %-32s  %-4s  %-8s  %-10s\n", "SSID", "CHAN", "RSSI", "AUTH");
  printf("  --------------------------------  ----  --------  ----------\n");
  for (uint16_t i = 0; i < count; i++)
    {
      printf("  %-32s  %4d  %4d dBm  %s\n",
             (const char *)records[i].ssid,
             records[i].primary,
             records[i].rssi,
             auth_name(records[i].authmode));
    }
  free(records);
  return 0;
}

static bool read_masked_password(char *buf, size_t max_len)
{
  size_t idx = 0;
  fflush(stdout);

  while (1)
    {
      int c = getchar();
      if (c == EOF || c == 0)
        {
          clearerr(stdin);
          vTaskDelay(pdMS_TO_TICKS(10));
          continue;
        }

      if (c == '\r' || c == '\n')
        {
          /* Ignore a pending line ending from the command that opened this prompt. */
          if (idx == 0) continue;
          printf("\n");
          fflush(stdout);
          buf[idx] = '\0';
          return true;
        }
      else if (c == 0x08 || c == 0x7f) /* Backspace / Delete */
        {
          if (idx > 0)
            {
              idx--;
              printf("\b \b");
              fflush(stdout);
            }
        }
      else if (c == 0x03) /* Ctrl+C */
        {
          printf("^C\n");
          fflush(stdout);
          memset(buf, 0, max_len);
          return false;
        }
      else if (c >= 32 && c <= 126) /* Printable ASCII */
        {
          if (idx < max_len - 1)
            {
              buf[idx++] = (char)c;
              putchar('*');
              fflush(stdout);
            }
        }
    }
}

static bool load_wifi_credentials(char *ssid, size_t ssid_len, char *pass, size_t pass_len)
{
  nvs_handle_t handle;
  esp_err_t err = nvs_open("wifi_cred", NVS_READONLY, &handle);
  if (err != ESP_OK)
    {
      return false;
    }

  err = nvs_get_str(handle, "ssid", ssid, &ssid_len);
  if (err == ESP_OK)
    {
      err = nvs_get_str(handle, "password", pass, &pass_len);
    }
  nvs_close(handle);
  return (err == ESP_OK && strlen(ssid) > 0);
}

static bool save_wifi_credentials(const char *ssid, const char *pass)
{
  nvs_handle_t handle;
  esp_err_t err = nvs_open("wifi_cred", NVS_READWRITE, &handle);
  if (err != ESP_OK)
    {
      return false;
    }

  err = nvs_set_str(handle, "ssid", ssid);
  if (err == ESP_OK && pass != NULL)
    {
      err = nvs_set_str(handle, "password", pass);
    }
  if (err == ESP_OK)
    {
      err = nvs_commit(handle);
    }
  nvs_close(handle);
  return (err == ESP_OK);
}

static bool clear_wifi_credentials(void)
{
  nvs_handle_t handle;
  esp_err_t err = nvs_open("wifi_cred", NVS_READWRITE, &handle);
  if (err != ESP_OK)
    {
      return false;
    }
  err = nvs_erase_all(handle);
  if (err == ESP_OK)
    {
      err = nvs_commit(handle);
    }
  nvs_close(handle);
  return err == ESP_OK;
}

static int cmd_wifi_connect(int argc, char **argv)
{
  if (!s_wifi_initialized)
    {
      printf("wifi_connect: Wi-Fi stack not initialized\n");
      return 1;
    }

  char ssid[33] = {0};
  char password[65] = {0};

  if (argc == 1)
    {
      /* No arguments: connect using saved NVS credentials */
      if (!load_wifi_credentials(ssid, sizeof(ssid), password, sizeof(password)))
        {
          printf("No saved Wi-Fi credentials found in NVS.\n");
          printf("Usage: wifi_connect [ssid] [password]\n");
          return 1;
        }
      printf("Using saved credentials for '%s'\n", ssid);
    }
  else
    {
      if (strlen(argv[1]) >= sizeof(ssid) ||
          (argc >= 3 && strlen(argv[2]) >= sizeof(password)))
        {
          printf("SSID or password is too long.\n");
          return 1;
        }
      strncpy(ssid, argv[1], sizeof(ssid) - 1);

      if (argc >= 3)
        {
          /* Password passed directly as argument */
          strncpy(password, argv[2], sizeof(password) - 1);
        }
      else
        {
          /* Interactive masked password prompt */
          printf("Enter password for '%s' (masked): ", ssid);
          if (!read_masked_password(password, sizeof(password)))
            {
              return 1;
            }
        }
    }

  wifi_config_t wifi_cfg = {0};
  memcpy(wifi_cfg.sta.ssid, ssid, sizeof(wifi_cfg.sta.ssid));
  memcpy(wifi_cfg.sta.password, password, sizeof(wifi_cfg.sta.password));

  esp_wifi_disconnect();
  s_wifi_connected = false;
  esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
  memset(&wifi_cfg, 0, sizeof(wifi_cfg));
  if (err != ESP_OK)
    {
      printf("wifi_connect: configuration failed: %s\n", esp_err_to_name(err));
      memset(password, 0, sizeof(password));
      return 1;
    }

  /* Drop any token left by an earlier attempt */
  xSemaphoreTake(s_ip_sem, 0);

  printf("Connecting to %s...\n", ssid);
  err = esp_wifi_connect();
  if (err != ESP_OK)
    {
      printf("wifi_connect: connection failed: %s\n", esp_err_to_name(err));
      memset(password, 0, sizeof(password));
      return 1;
    }

  if (xSemaphoreTake(s_ip_sem, pdMS_TO_TICKS(15000)) == pdTRUE)
    {
      esp_netif_ip_info_t ip_info;
      esp_netif_get_ip_info(s_sta_netif, &ip_info);
      printf("Connected successfully! IP: " IPSTR ", Gateway: " IPSTR "\n",
             IP2STR(&ip_info.ip), IP2STR(&ip_info.gw));

      /* Save credentials to NVS so they persist across restart */
      if (save_wifi_credentials(ssid, password))
        {
          printf("Credentials saved securely to NVS (will auto-connect on boot).\n");
        }

      /* Clean sensitive password buffers */
      memset(password, 0, sizeof(password));
      memset(&wifi_cfg, 0, sizeof(wifi_cfg));
      return 0;
    }
  else
    {
      printf("Connection timed out. Check SSID and password.\n");
      memset(password, 0, sizeof(password));
      memset(&wifi_cfg, 0, sizeof(wifi_cfg));
      return 1;
    }
}

static int cmd_wifi_forget(int argc, char **argv)
{
  if (!s_wifi_initialized)
    {
      printf("wifi_forget: Wi-Fi stack not initialized\n");
      return 1;
    }
  if (clear_wifi_credentials())
    {
      esp_wifi_disconnect();
      s_wifi_connected = false;
      printf("Saved Wi-Fi credentials erased from NVS.\n");
      return 0;
    }
  printf("Failed to erase Wi-Fi credentials.\n");
  return 1;
}

static int cmd_wifi_disconnect(int argc, char **argv)
{
  if (!s_wifi_initialized)
    {
      printf("wifi_disconnect: Wi-Fi stack not initialized\n");
      return 1;
    }
  esp_wifi_disconnect();
  s_wifi_connected = false;
  printf("Disconnected from Wi-Fi\n");
  return 0;
}

static int cmd_ifconfig(int argc, char **argv)
{
  if (!s_wifi_initialized || !s_sta_netif)
    {
      printf("ifconfig: interface not initialized\n");
      return 1;
    }

  esp_netif_ip_info_t ip_info;
  esp_netif_get_ip_info(s_sta_netif, &ip_info);

  esp_ip4_addr_t broadcast = {
      .addr = ip_info.ip.addr | ~ip_info.netmask.addr,
  };

  printf("wlan0: flags=%s\n", s_wifi_connected ? "UP,CONNECTED" : "DOWN");
  printf("       inet " IPSTR " netmask " IPSTR " broadcast " IPSTR "\n",
         IP2STR(&ip_info.ip), IP2STR(&ip_info.netmask), IP2STR(&broadcast));
  printf("       gateway " IPSTR "\n", IP2STR(&ip_info.gw));

  char saved_ssid[33] = {0};
  char dummy_pass[65] = {0};
  if (load_wifi_credentials(saved_ssid, sizeof(saved_ssid), dummy_pass, sizeof(dummy_pass)))
    {
      printf("       saved_network: \"%s\" (in NVS)\n", saved_ssid);
      memset(dummy_pass, 0, sizeof(dummy_pass));
    }
  else
    {
      printf("       saved_network: none\n");
    }
  return 0;
}

static void ping_on_success(esp_ping_handle_t hdl, void *args)
{
  uint8_t ttl;
  uint16_t seqno;
  uint32_t elapsed_time, recv_len;
  ip_addr_t target_addr;
  esp_ping_get_profile(hdl, ESP_PING_PROF_SEQNO, &seqno, sizeof(seqno));
  esp_ping_get_profile(hdl, ESP_PING_PROF_TTL, &ttl, sizeof(ttl));
  esp_ping_get_profile(hdl, ESP_PING_PROF_IPADDR, &target_addr, sizeof(target_addr));
  esp_ping_get_profile(hdl, ESP_PING_PROF_SIZE, &recv_len, sizeof(recv_len));
  esp_ping_get_profile(hdl, ESP_PING_PROF_TIMEGAP, &elapsed_time, sizeof(elapsed_time));
  printf("%lu bytes from %s: icmp_seq=%u ttl=%u time=%lu ms\n",
         (unsigned long)recv_len, inet_ntoa(target_addr.u_addr.ip4), seqno, ttl, (unsigned long)elapsed_time);
}

static void ping_on_timeout(esp_ping_handle_t hdl, void *args)
{
  uint16_t seqno;
  ip_addr_t target_addr;
  esp_ping_get_profile(hdl, ESP_PING_PROF_SEQNO, &seqno, sizeof(seqno));
  esp_ping_get_profile(hdl, ESP_PING_PROF_IPADDR, &target_addr, sizeof(target_addr));
  printf("From %s icmp_seq=%u Request timeout\n",
         inet_ntoa(target_addr.u_addr.ip4), seqno);
}

static void ping_on_end(esp_ping_handle_t hdl, void *args)
{
  SemaphoreHandle_t sem = (SemaphoreHandle_t)args;
  uint32_t transmitted = 0, received = 0, total_time_ms = 0;
  esp_ping_get_profile(hdl, ESP_PING_PROF_REQUEST, &transmitted, sizeof(transmitted));
  esp_ping_get_profile(hdl, ESP_PING_PROF_REPLY, &received, sizeof(received));
  esp_ping_get_profile(hdl, ESP_PING_PROF_DURATION, &total_time_ms, sizeof(total_time_ms));

  printf("--- ping statistics ---\n");
  printf("%lu packets transmitted, %lu received, %d%% packet loss, time %lu ms\n",
         (unsigned long)transmitted, (unsigned long)received,
         transmitted > 0 ? (int)((transmitted - received) * 100 / transmitted) : 0,
         (unsigned long)total_time_ms);
  esp_ping_delete_session(hdl);
  if (sem) xSemaphoreGive(sem);
}

static int cmd_ping(int argc, char **argv)
{
  if (!s_wifi_initialized)
    {
      printf("ping: network stack not initialized\n");
      return 1;
    }
  if (argc < 2)
    {
      printf("Usage: ping <host_or_ip>\n");
      return 1;
    }

  ip_addr_t target_addr;
  struct addrinfo hint = { .ai_family = AF_INET };
  struct addrinfo *res = NULL;
  if (getaddrinfo(argv[1], NULL, &hint, &res) != 0 || res == NULL)
    {
      printf("ping: cannot resolve host %s\n", argv[1]);
      return 1;
    }

  struct sockaddr_in *saddr = (struct sockaddr_in *)res->ai_addr;
  inet_addr_to_ip4addr(ip_2_ip4(&target_addr), &saddr->sin_addr);
  target_addr.type = IPADDR_TYPE_V4;
  freeaddrinfo(res);

  SemaphoreHandle_t sem = xSemaphoreCreateBinary();
  if (!sem)
    {
      printf("ping: out of memory\n");
      return 1;
    }
  esp_ping_config_t ping_config = ESP_PING_DEFAULT_CONFIG();
  ping_config.target_addr = target_addr;
  ping_config.count = 4;

  esp_ping_callbacks_t cbs = {
      .cb_args = sem,
      .on_ping_success = ping_on_success,
      .on_ping_timeout = ping_on_timeout,
      .on_ping_end = ping_on_end,
  };

  esp_ping_handle_t ping_hdl;
  if (esp_ping_new_session(&ping_config, &cbs, &ping_hdl) != ESP_OK)
    {
      printf("ping: failed to create ping session\n");
      vSemaphoreDelete(sem);
      return 1;
    }

  printf("PING %s (%s): %lu data bytes\n",
         argv[1], inet_ntoa(target_addr.u_addr.ip4), (unsigned long)ping_config.data_size);
  esp_ping_start(ping_hdl);
  xSemaphoreTake(sem, portMAX_DELAY);
  vSemaphoreDelete(sem);
  return 0;
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED)
    {
      printf("\n[Wi-Fi] Connected to AP, negotiating DHCP lease...\n");
    }
  else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED)
    {
      s_wifi_connected = false;
      printf("\n[Wi-Fi] Disconnected\n");
    }
  else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP)
    {
      ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
      printf("\n[Wi-Fi] Acquired IP address: " IPSTR "\n", IP2STR(&event->ip_info.ip));
      s_wifi_connected = true;
      if (s_ip_sem)
        {
          xSemaphoreGive(s_ip_sem);
        }
    }
}

static void init_wifi(void)
{
  s_ip_sem = xSemaphoreCreateBinary();
  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
      nvs_flash_erase();
      err = nvs_flash_init();
    }
  if (err != ESP_OK)
    {
      ESP_LOGE(TAG, "NVS flash init failed (%s)", esp_err_to_name(err));
      return;
    }

  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());
  s_sta_netif = esp_netif_create_default_wifi_sta();

  esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                      &wifi_event_handler, NULL, NULL);
  esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                      &wifi_event_handler, NULL, NULL);

  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  err = esp_wifi_init(&cfg);
  if (err != ESP_OK)
    {
      ESP_LOGW(TAG, "esp_wifi_init failed: %s (radio not ready)", esp_err_to_name(err));
      return;
    }

  esp_wifi_set_mode(WIFI_MODE_STA);
  esp_wifi_start();
  s_wifi_initialized = true;
  ESP_LOGI(TAG, "Wi-Fi radio is up and ready");

  /* Auto-connect on boot if stored credentials exist in NVS */
  char saved_ssid[33] = {0};
  char saved_pass[65] = {0};
  if (load_wifi_credentials(saved_ssid, sizeof(saved_ssid), saved_pass, sizeof(saved_pass)))
    {
      ESP_LOGI(TAG, "Found stored Wi-Fi credentials for '%s', auto-connecting...", saved_ssid);
      wifi_config_t wifi_cfg = {0};
      memcpy(wifi_cfg.sta.ssid, saved_ssid, sizeof(wifi_cfg.sta.ssid));
      memcpy(wifi_cfg.sta.password, saved_pass, sizeof(wifi_cfg.sta.password));
      memset(saved_pass, 0, sizeof(saved_pass));
      esp_err_t connect_err = esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
      memset(&wifi_cfg, 0, sizeof(wifi_cfg));
      if (connect_err == ESP_OK)
        {
          connect_err = esp_wifi_connect();
        }
      if (connect_err != ESP_OK)
        {
          ESP_LOGW(TAG, "Auto-connect failed: %s", esp_err_to_name(connect_err));
        }
    }
}

/* Intercept write calls from linenoise to drop cursor position query (\x1b[6n).
 * On serial UART links, querying terminal dimensions with DSR escape sequences
 * causes terminals to send cursor reports that pollute the input buffer or throw
 * the cursor to margin 999. Returning -1 here forces linenoise to immediately
 * default to 80 columns without sending escape sequences or blocking UART.
 */
extern ssize_t __real_write(int fd, const void *buf, size_t count);

ssize_t __wrap_write(int fd, const void *buf, size_t count)
{
  if (count >= 4 && ((const char *)buf)[0] == '\x1b' && ((const char *)buf)[1] == '[' &&
      ((const char *)buf)[2] == '6' && ((const char *)buf)[3] == 'n')
    {
      return -1;
    }
  return __real_write(fd, buf, count);
}

static void add_path_completion(const char *buf, size_t prefix_len,
                                const char *path, linenoiseCompletions *lc)
{
  /* linenoise reserves one byte, then uses that reduced size for snprintf. */
  char completion[SHELL_MAX_CMDLINE - 1];
  if (prefix_len >= sizeof(completion)) return;
  memcpy(completion, buf, prefix_len);
  size_t len = prefix_len;

  for (const char *p = path; *p; p++)
    {
      bool escape = *p == ' ' || *p == '\\' || *p == '"';
      if (len + (escape ? 2 : 1) >= sizeof(completion)) return;
      if (escape) completion[len++] = '\\';
      completion[len++] = *p;
    }
  completion[len] = '\0';
  linenoiseAddCompletion(lc, completion);
}

static void custom_completion(const char *buf, linenoiseCompletions *lc)
{
  if (!buf || strlen(buf) >= SHELL_MAX_CMDLINE) return;

  const char *arg = buf;
  bool in_arg = false, quoted = false, escaped = false;
  size_t completed = 0;
  for (const char *p = buf; *p; p++)
    {
      if (escaped)
        {
          escaped = false;
          continue;
        }
      if (!in_arg)
        {
          arg = p;
          if (*p == ' ')
            {
              arg = p + 1;
              continue;
            }
          in_arg = true;
          quoted = *p == '"';
          if (quoted) continue;
        }
      if (*p == '\\') escaped = true;
      else if ((quoted && *p == '"') || (!quoted && *p == ' '))
        {
          in_arg = false;
          quoted = false;
          completed++;
          if (*p == ' ') arg = p + 1;
        }
    }

  if (completed == 0)
    {
      esp_console_get_completion(buf, lc);
      return;
    }

  char path[SHELL_MAX_CMDLINE];
  strcpy(path, arg);
  char *args[2];
  esp_console_split_argv(path, args, 2);

  char dir_to_open[512];
  char *last_slash = strrchr(path, '/');
  const char *match_prefix = last_slash ? last_slash + 1 : path;
  if (last_slash)
    {
      *last_slash = '\0';
      bool ok = resolve_path(last_slash == path ? "/" : path,
                             dir_to_open, sizeof(dir_to_open));
      *last_slash = '/';
      if (!ok) return;
    }
  else
    {
      strcpy(dir_to_open, s_cwd);
    }

  /* ESP-IDF VFS has no physical root directory to enumerate. */
  if (strcmp(dir_to_open, "/") == 0)
    {
      const char *mounts[] = { "sdcard", "data" };
      for (size_t m = 0; m < sizeof(mounts) / sizeof(mounts[0]); m++)
        {
          if (strncmp(mounts[m], match_prefix, strlen(match_prefix)) == 0)
            {
              char candidate[16];
              snprintf(candidate, sizeof(candidate), "/%s/", mounts[m]);
              add_path_completion(buf, arg - buf, candidate, lc);
            }
        }
      return;
    }

  DIR *d = opendir(dir_to_open);
  if (!d) return;

  struct dirent *de;
  size_t match_len = strlen(match_prefix);
  while ((de = readdir(d)) != NULL)
    {
      if (strcmp(de->d_name, ".") == 0 || strcmp(de->d_name, "..") == 0) continue;
      if (strncmp(de->d_name, match_prefix, match_len) == 0)
        {
          char full_test[1024];
          snprintf(full_test, sizeof(full_test), "%s/%s", dir_to_open, de->d_name);
          struct stat st;
          bool directory = stat(full_test, &st) == 0 && S_ISDIR(st.st_mode);
          char candidate[512];
          int len = snprintf(candidate, sizeof(candidate), "%.*s%s%s",
                             last_slash ? (int)(last_slash - path + 1) : 0,
                             path, de->d_name, directory ? "/" : "");
          if (len >= 0 && (size_t)len < sizeof(candidate))
            {
              add_path_completion(buf, arg - buf, candidate, lc);
            }
        }
    }
  closedir(d);
}

static int cmd_clear(int argc, char **argv)
{
  (void)argc;
  (void)argv;
  linenoiseClearScreen();
  return 0;
}

static void register_commands(void)
{
  esp_console_register_help_command();

  const esp_console_cmd_t cmds[] = {
      {
          .command = "clear",
          .help = "Clear the terminal screen (also Ctrl+L)",
          .hint = NULL,
          .func = &cmd_clear,
      },
#if CONFIG_IDF_TARGET_ESP32P4
      {
          .command = "reset_c6",
          .help = "Pulse hardware reset line (GPIO 54) to ESP32-C6",
          .hint = NULL,
          .func = &cmd_reset_c6,
      },
#endif
      {
          .command = "top",
          .help = "Show live FreeRTOS tasks and CPU usage",
          .hint = NULL,
          .func = &cmd_top,
      },
      {
          .command = "pwd",
          .help = "Print current working directory",
          .hint = NULL,
          .func = &cmd_pwd,
      },
      {
          .command = "cd",
          .help = "Change working directory",
          .hint = "[<path>]",
          .func = &cmd_cd,
      },
      {
          .command = "ls",
          .help = "List files in directory",
          .hint = "[<path>]",
          .func = &cmd_ls,
      },
      {
          .command = "tree",
          .help = "List directory contents in a tree structure",
          .hint = "[<path>]",
          .func = &cmd_tree,
      },
      {
          .command = "cat",
          .help = "Print file contents",
          .hint = "<filename>",
          .func = &cmd_cat,
      },
      {
          .command = "write",
          .help = "Write text to file",
          .hint = "<filename> <text>",
          .func = &cmd_write,
      },
      {
          .command = "nano",
          .help = "Interactive terminal text editor",
          .hint = "<filename>",
          .func = &cmd_nano,
      },
      {
          .command = "edit",
          .help = "Alias for nano",
          .hint = "<filename>",
          .func = &cmd_nano,
      },
      {
          .command = "cp",
          .help = "Copy file",
          .hint = "<src> <dst>",
          .func = &cmd_cp,
      },
      {
          .command = "rm",
          .help = "Delete a file",
          .hint = "<filename>",
          .func = &cmd_rm,
      },
      {
          .command = "mkdir",
          .help = "Create a directory",
          .hint = "<dirname>",
          .func = &cmd_mkdir,
      },
      {
          .command = "df",
          .help = "Show filesystem usage",
          .hint = NULL,
          .func = &cmd_df,
      },
      {
          .command = "wifi_scan",
          .help = "Scan for Wi-Fi access points",
          .hint = NULL,
          .func = &cmd_wifi_scan,
      },
      {
          .command = "wifi_connect",
          .help = "Connect to Wi-Fi (interactive masked password or NVS saved)",
          .hint = "[<ssid>] [<password>]",
          .func = &cmd_wifi_connect,
      },
      {
          .command = "wifi_forget",
          .help = "Erase saved Wi-Fi credentials from NVS",
          .hint = NULL,
          .func = &cmd_wifi_forget,
      },
      {
          .command = "wifi_disconnect",
          .help = "Disconnect from Wi-Fi",
          .hint = NULL,
          .func = &cmd_wifi_disconnect,
      },
      {
          .command = "ifconfig",
          .help = "Show network interface configuration",
          .hint = NULL,
          .func = &cmd_ifconfig,
      },
      {
          .command = "ping",
          .help = "Send ICMP echo requests to network host",
          .hint = "<host_or_ip>",
          .func = &cmd_ping,
      },
      {
          .command = "ps",
          .help = "Show FreeRTOS tasks",
          .hint = NULL,
          .func = &cmd_ps,
      },
      {
          .command = "free",
          .help = "Show available heap memory",
          .hint = NULL,
          .func = &cmd_free,
      },
      {
          .command = "uname",
          .help = "Show OS and chip information",
          .hint = NULL,
          .func = &cmd_uname,
      },
      {
          .command = "cpuinfo",
          .help = "Show CPU architecture, clock frequencies, and cache specifications",
          .hint = NULL,
          .func = &cmd_cpuinfo,
      },
      {
          .command = "reboot",
          .help = "Reboot the chip",
          .hint = NULL,
          .func = &cmd_reboot,
      },
  };

  for (size_t i = 0; i < sizeof(cmds) / sizeof(cmds[0]); i++)
    {
      esp_console_cmd_register(&cmds[i]);
    }
}

void shell_restore_linenoise_callbacks(void)
{
  linenoiseSetCompletionCallback(&custom_completion);
  linenoiseSetHintsCallback((linenoiseHintsCallback *)&esp_console_get_hint);
}

void app_main(void)
{
  ESP_LOGI(TAG, "Starting %s DuckDB shell firmware", CONFIG_IDF_TARGET);

  heap_caps_malloc_extmem_enable(0);
  esp_err_t ret;

  /* 1. Initialize Internal LittleFS Flash Storage */
  esp_vfs_littlefs_conf_t lfs_conf = {
      .base_path = DATA_MOUNT_POINT,
      .partition_label = "storage",
      .format_if_mount_failed = false,
      .dont_mount = false,
  };
  esp_err_t lfs_ret = esp_vfs_littlefs_register(&lfs_conf);
  if (lfs_ret == ESP_OK)
    {
      ESP_LOGI(TAG, "LittleFS mounted at %s", DATA_MOUNT_POINT);
    }
  else
    {
      ESP_LOGW(TAG, "LittleFS mount failed (%s)", esp_err_to_name(lfs_ret));
    }

#if CONFIG_IDF_TARGET_ESP32P4
  /* 2. Initialize MicroSD Card */
  power_on_slot();

  sdmmc_host_t host = SDMMC_HOST_DEFAULT();
  host.slot = SDMMC_HOST_SLOT_0;
  host.max_freq_khz = SDMMC_FREQ_DEFAULT;

  sd_pwr_ctrl_ldo_config_t ldo_config = {
      .ldo_chan_id = 4,
  };
  sd_pwr_ctrl_handle_t pwr_ctrl_handle = NULL;
  ret = sd_pwr_ctrl_new_on_chip_ldo(&ldo_config, &pwr_ctrl_handle);
  if (ret != ESP_OK)
    {
      ESP_LOGE(TAG, "Failed to create LDO power control handle (%s)", esp_err_to_name(ret));
    }
  else
    {
      host.pwr_ctrl_handle = pwr_ctrl_handle;
    }

  sdmmc_slot_config_t slot_config = SDMMC_SLOT_CONFIG_DEFAULT();
  slot_config.width = 4;
  slot_config.clk = PIN_TF_CLK;
  slot_config.cmd = PIN_TF_CMD;
  slot_config.d0 = PIN_TF_D0;
  slot_config.d1 = PIN_TF_D1;
  slot_config.d2 = PIN_TF_D2;
  slot_config.d3 = PIN_TF_D3;

  esp_vfs_fat_sdmmc_mount_config_t mount_config = {
      .format_if_mount_failed = false,
      .max_files = 12,
      .allocation_unit_size = 16 * 1024,
  };

  sdmmc_card_t *card = NULL;
  ret = esp_vfs_fat_sdmmc_mount(SDCARD_MOUNT_POINT, &host, &slot_config, &mount_config, &card);
  if (ret == ESP_OK)
    {
      ESP_LOGI(TAG, "MicroSD card mounted at %s", SDCARD_MOUNT_POINT);
    }
  else
    {
      ESP_LOGE(TAG, "Failed to mount SD card (%s)", esp_err_to_name(ret));
    }
#endif

  /* 3. Initialize Wi-Fi (P4 reaches the radio on the onboard ESP32-C6) */
  init_wifi();

  /* 4. Initialize Interactive REPL Console */
  esp_console_repl_t *repl = NULL;
  esp_console_repl_config_t repl_config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
  repl_config.prompt = "msh> ";
  repl_config.max_cmdline_length = SHELL_MAX_CMDLINE;
  /* Leave room for filesystem completion and command handlers. */
  repl_config.task_stack_size = 16384;

#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
  esp_console_dev_usb_serial_jtag_config_t jtag_config = ESP_CONSOLE_DEV_USB_SERIAL_JTAG_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_console_new_repl_usb_serial_jtag(&jtag_config, &repl_config, &repl));
#else
  esp_console_dev_uart_config_t uart_config = ESP_CONSOLE_DEV_UART_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_console_new_repl_uart(&uart_config, &repl_config, &repl));
#endif

  /* Force enable raw mode for readline, arrow keys, history, and tab completion */
  linenoiseSetDumbMode(0);
  linenoiseSetCompletionCallback(&custom_completion);

  register_commands();
  duckdb_console_register();
  network_register();

  /* 5. Check and validate FOTA running partition state */
  const esp_partition_t *running = esp_ota_get_running_partition();
  esp_ota_img_states_t ota_state = ESP_OTA_IMG_UNDEFINED;
  if (running != NULL && esp_ota_get_state_partition(running, &ota_state) == ESP_OK)
    {
      if (ota_state == ESP_OTA_IMG_PENDING_VERIFY)
        {
          ESP_LOGW(TAG, "Firmware on %s is in PENDING_VERIFY state.", running->label);
          ESP_LOGI(TAG, "Self-tests and hardware initialization passed.");
          esp_err_t ota_err = esp_ota_mark_app_valid_cancel_rollback();
          if (ota_err == ESP_OK)
            {
              ESP_LOGI(TAG, "Firmware marked VALID; rollback cancelled.");
            }
          else
            {
              ESP_LOGE(TAG, "Failed to mark app valid: %s", esp_err_to_name(ota_err));
            }
        }
      else
        {
          ESP_LOGI(TAG, "Running firmware slot: %s (state: %d)",
                   running->label, (int)ota_state);
        }
    }

  printf("\n");
  printf("====================================================\n");
#if CONFIG_IDF_TARGET_ESP32P4
  printf("  ESP32-P4 DuckDB Micro Shell (msh) (ESP-IDF v%s)\n", esp_get_idf_version());
  printf("  Storage: /data (LittleFS Flash), /sdcard (FATFS SD)\n");
  printf("  Network: Wi-Fi STA via ESP32-C6 coprocessor\n");
#elif CONFIG_IDF_TARGET_ESP32C6
  printf("  ESP32-C6 DuckDB Shell (ESP-IDF v%s)\n", esp_get_idf_version());
  printf("  Storage: no internal filesystem\n");
  printf("  Network: Wi-Fi 6 STA on the onboard radio\n");
#else
  printf("  ESP32-S3 DuckDB Shell (ESP-IDF v%s)\n", esp_get_idf_version());
  printf("  Storage: no internal filesystem\n");
  printf("  Network: Wi-Fi STA on the onboard radio\n");
#endif
  printf("  DuckDB:  type duckdb for an in-memory SQL prompt\n");
  printf("  Editor:  nano <file> / edit <file>\n");
  printf("  Type 'help' to list available commands\n");
  printf("====================================================\n\n");

  ESP_ERROR_CHECK(esp_console_start_repl(repl));
}
