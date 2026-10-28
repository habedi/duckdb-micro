"""Run the shell's Wi-Fi helpers on the host with simulated SDK calls."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


STUBS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int esp_err_t;
typedef int nvs_handle_t;
#define ESP_OK 0
#define NVS_READONLY 0
#define NVS_READWRITE 1
#define WIFI_IF_STA 0
#define pdTRUE 1
#define pdMS_TO_TICKS(ms) (ms)
#define IPSTR "%d"
#define IP2STR(ip) (*(ip))
#define ESP_LOGI(...) ((void)0)
typedef struct { struct { unsigned char ssid[32], password[64]; } sta; } wifi_config_t;
typedef struct { int ip, gw; } esp_netif_ip_info_t;
static bool s_wifi_initialized = true, s_wifi_connected;
static int s_ip_sem, s_sta_netif;
static int open_error, erase_error, commit_error, erase_calls, commit_calls, close_calls;
static int config_calls, disconnect_calls;
static wifi_config_t captured;
static char stored_ssid[33], stored_password[65];
static int nvs_open(const char *name, int mode, nvs_handle_t *handle)
{ (void)name; (void)mode; *handle = 1; return open_error; }
static int nvs_get_str(int handle, const char *key, char *out, size_t *size)
{
  (void)handle;
  const char *value = strcmp(key, "ssid") == 0 ? stored_ssid : stored_password;
  size_t len = strlen(value) + 1;
  if (len > *size) return -1;
  memcpy(out, value, len);
  return ESP_OK;
}
static int nvs_set_str(int handle, const char *key, const char *value)
{ (void)handle; (void)key; (void)value; return ESP_OK; }
static int nvs_erase_all(int handle)
{ (void)handle; erase_calls++; return erase_error; }
static int nvs_commit(int handle)
{ (void)handle; commit_calls++; return commit_error; }
static void nvs_close(int handle) { (void)handle; close_calls++; }
static void vTaskDelay(int ticks) { (void)ticks; abort(); }
static int esp_wifi_disconnect(void) { disconnect_calls++; return ESP_OK; }
static int esp_wifi_set_config(int iface, const wifi_config_t *cfg)
{ (void)iface; captured = *cfg; config_calls++; return ESP_OK; }
static int esp_wifi_connect(void) { return ESP_OK; }
static int xSemaphoreTake(int sem, int ticks) { (void)sem; return ticks != 0; }
static int esp_netif_get_ip_info(int netif, esp_netif_ip_info_t *info)
{ (void)netif; info->ip = 1; info->gw = 2; return ESP_OK; }
"""

CHECKS = r"""
int main(int argc, char **argv)
{
  assert(argc == 2);
  if (strcmp(argv[1], "erase") == 0) {
    s_wifi_initialized = false;
    assert(cmd_wifi_forget(1, NULL) == 1);
    assert(erase_calls == 0 && commit_calls == 0 && close_calls == 0);
    s_wifi_initialized = true;
    open_error = -1;
    assert(!clear_wifi_credentials());
    assert(erase_calls == 0 && commit_calls == 0 && close_calls == 0);
    open_error = 0;
    erase_error = -1;
    assert(!clear_wifi_credentials());
    assert(erase_calls == 1 && commit_calls == 0 && close_calls == 1);
    erase_error = 0;
    commit_error = -1;
    assert(!clear_wifi_credentials());
    assert(commit_calls == 1 && close_calls == 2);
    commit_error = 0;
    assert(clear_wifi_credentials());
    assert(commit_calls == 2 && close_calls == 3);
  } else if (strcmp(argv[1], "credentials") == 0) {
    memset(stored_ssid, 's', 32);
    memset(stored_password, 'a', 64);
    char *args[] = {"wifi_connect", stored_ssid, stored_password};
    assert(cmd_wifi_connect(3, args) == 0);
    assert(memcmp(captured.sta.ssid, stored_ssid, 32) == 0);
    assert(memcmp(captured.sta.password, stored_password, 64) == 0);
    assert(cmd_wifi_connect(1, args) == 0);
    assert(memcmp(captured.sta.ssid, stored_ssid, 32) == 0);
    assert(memcmp(captured.sta.password, stored_password, 64) == 0);
    boot_connect();
    assert(memcmp(captured.sta.ssid, stored_ssid, 32) == 0);
    assert(memcmp(captured.sta.password, stored_password, 64) == 0);
    args[1] = "short";
    args[2] = "";
    assert(cmd_wifi_connect(3, args) == 0);
    assert(memcmp(captured.sta.ssid, "short", 5) == 0);
    for (int i = 5; i < 32; i++) assert(captured.sta.ssid[i] == 0);
    for (int i = 0; i < 64; i++) assert(captured.sta.password[i] == 0);
  } else if (strcmp(argv[1], "oversized") == 0) {
    char ssid[34], password[66];
    memset(ssid, 's', 33); ssid[33] = 0;
    memset(password, 'a', 65); password[65] = 0;
    char *args[] = {"wifi_connect", ssid, "password"};
    assert(cmd_wifi_connect(3, args) == 1);
    args[1] = "network"; args[2] = password;
    assert(cmd_wifi_connect(3, args) == 1);
    assert(config_calls == 0 && disconnect_calls == 0);
  } else if (strcmp(argv[1], "prompt") == 0) {
    char password[65];
    assert(read_masked_password(password, sizeof(password)));
    assert(strcmp(password, "ac") == 0);
    assert(!read_masked_password(password, sizeof(password)));
    for (int i = 0; i < 65; i++) assert(password[i] == 0);
    assert(read_masked_password(password, sizeof(password)));
    for (int i = 0; i < 64; i++) assert(password[i] == 'a');
    assert(password[64] == 0);
  } else {
    abort();
  }
  return 0;
}
"""


class WifiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        source = (Path(__file__).parent / "../main/main.c").read_text()
        # Compile the real helpers without the unrelated board and shell code.
        start = source.index("static bool read_masked_password(")
        end = source.index("static int cmd_ifconfig(", start)
        boot_start = source.index("  /* Auto-connect on boot")
        boot_end = source.index("\n/* Intercept write calls", boot_start)
        boot = "static void boot_connect(void) {\n" + source[boot_start:boot_end]
        harness = Path(cls.directory.name) / "wifi.c"
        harness.write_text(STUBS + source[start:end] + boot + CHECKS)
        cls.binary = Path(cls.directory.name) / "test_wifi"
        command = shlex.split(os.environ.get("CC", "cc"))
        for name in ("CFLAGS", "SANFLAGS"):
            command += shlex.split(os.environ.get(name, ""))
        command += [str(harness), "-o", str(cls.binary)]
        command += shlex.split(os.environ.get("LDFLAGS", ""))
        subprocess.run(command, check=True)

    def run_case(self, name, data=""):
        return subprocess.run(
            [str(self.binary), name], input=data, text=True,
            capture_output=True, check=True,
        ).stdout

    def test_erase_errors(self):
        self.run_case("erase")

    def test_maximum_and_empty_credentials(self):
        self.run_case("credentials")

    def test_oversized_credentials_do_not_disconnect(self):
        self.run_case("oversized")

    def test_masked_input_backspace_cancel_and_limit(self):
        output = self.run_case("prompt", "ab\bc\nsecret\x03" + "a" * 64 + "\n")
        self.assertNotIn("secret", output)
        self.assertNotIn("ac", output)
        self.assertEqual(output.count("*"), 73)
