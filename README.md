## DuckDB Micro

[![Tests](https://img.shields.io/github/actions/workflow/status/habedi/duckdb-micro/tests.yml?label=tests&style=flat&labelColor=282c34&logo=github&logoColor=white)](https://github.com/habedi/duckdb-micro/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-007ec6?style=flat&labelColor=282c34&logo=open-source-initiative&logoColor=white)](https://github.com/habedi/duckdb-micro/blob/main/LICENSE)
[![Release](https://img.shields.io/github/v/release/habedi/duckdb-micro?include_prereleases&label=firmware-release&style=flat&labelColor=282c34&logo=github&sort=semver)](https://github.com/habedi/duckdb-micro/releases)

---

DuckDB Micro is an experimental DuckDB 2.0 port to ESP32-P4 (more specifically,
the [Waveshare ESP32-P4-WIFI6](https://www.waveshare.com/wiki/ESP32-P4-WIFI6) board; see image below).

<div align="center">
  <img alt="MicroShell" src="docs/assets/images/esp32-p4-wifi6.png" width="40%">
</div>

### Why?

DuckDB normally needs at least 125 MB of RAM per thread to work properly, and it officially only supports 64-bit architectures.
This project is an attempt to see how much of DuckDB's functionality can be made available on a low-resource device like an ESP32 microcontroller that
lacks a lot of conventional features like a lot of (fast) memory, a full-featured virtual memory system, etc.

See the [ROADMAP.md](ROADMAP.md) for the list of implemented and planned features.

**This project is very experimental.
You can download the latest pre-built firmware from the [releases page](https://github.com/habedi/duckdb-micro/releases).**

---

### Quickstart

To follow the steps below, you need to have Git and [Nix](https://nixos.org/download/) (with flakes enabled) installed on your machine.
The instructions are tested on Linux, and the firmware is tested on an ESP32-P4-WIFI6 board with silicon revision v1.3 with 32 MB PSRAM and 32 MB
NOR flash.

#### 1. Clone the repository

```shell
git clone --depth=1 --recursive https://github.com/habedi/duckdb-micro.git
cd duckdb-micro
```

#### 2. Set up the development environment

```shell
nix develop
```

#### 3. Build the (MicroShell) firmware

```shell
make espidf-install
make shell-build # This may take a while
```

#### 4. Flash the board

```shell
make flash TTY=/dev/ttyACM0
```

The board resets and starts the firmware after flashing.

#### 5. Connect to the board

```shell
make console TTY=/dev/ttyACM0
```

Press Enter twice after opening the console to get to the MicroShell (`msh>`) prompt.

<div align="center">
  <img alt="MicroShell" src="docs/assets/images/msh-01.png" width="80%">
</div>

> [!NOTE]
> Serial port access needs membership in the `dialout` group (which can be set up with something like `sudo usermod -aG dialout $USER`).
> Also, change `ttyACM0` to the appropriate serial port for your system, if needed.

---

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to make a contribution.

### License

This project is licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)).
