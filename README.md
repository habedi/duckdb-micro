## DuckDB Micro

[![Tests](https://img.shields.io/github/actions/workflow/status/habedi/duckdb-micro/tests.yml?label=tests&style=flat&labelColor=282c34&logo=github&logoColor=white)](https://github.com/habedi/duckdb-micro/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-007ec6?style=flat&labelColor=282c34&logo=open-source-initiative&logoColor=white)](https://github.com/habedi/duckdb-micro/blob/main/LICENSE)
[![Release](https://img.shields.io/github/v/release/habedi/duckdb-micro?include_prereleases&label=firmware-release&style=flat&labelColor=282c34&logo=github&sort=semver)](https://github.com/habedi/duckdb-micro/releases)

---

> [!WARNING]
> **This project is very experimental.**

DuckDB Micro is an experimental (and partial) port of DuckDB to ESP32-P4 (more specifically,
the [Waveshare ESP32-P4-WIFI6](https://www.waveshare.com/wiki/ESP32-P4-WIFI6) board; see image below).

<div align="center">
  <img alt="Waveshare ESP32-P4-WIFI6" src="docs/assets/images/esp32-p4-wifi6.png" width="40%">
</div>

### Why?

DuckDB normally needs at least 125 MB of RAM per thread to work properly, and it officially only supports 64-bit architectures.
This project is an attempt to see how much of DuckDB's functionality can be made available on a low-resource device like an ESP32 microcontroller that
lacks a lot of conventional features like a lot of (fast) memory, a full-featured virtual memory system, etc.

### Feature Comparisons

| Feature           | DuckDB                                         | DuckDB Micro                                              |
|-------------------|------------------------------------------------|-----------------------------------------------------------|
| Platform          | Desktop and server (Linux, Windows, and macOS) | ESP32-P4 (FreeRTOS)                                       |
| Architecture      | 64-bit (x86_64 and AArch64)                    | 32-bit (RISC-V)                                           |
| Query parallelism | Multi-core                                     | Single thread (up to 2)                                   |
| Buffer memory     | Host RAM (configurable)                        | 8 MB (PSRAM)                                              |
| Storage           | In-memory and persistent                       | In-memory, microSD (FATFS), and internal flash (LittleFS) |
| Disk spilling     | Supported                                      | Disabled                                                  |
| Data formats      | CSV, JSON, Parquet, and more                   | CSV, JSON, and Parquet                                    |
| Extensions        | Supported                                      | Core functions, JSON, and Parquet only                    |
| Remote files      | Direct over HTTP(S) access (via `httpfs`)      | Download to local storage first                           |
| User interface    | CLI and language APIs                          | MicroShell CLI and optional REST API                      |

See [ROADMAP.md](ROADMAP.md) for the list of implemented and planned features.

**You can download the latest pre-built firmware from the [releases page](https://github.com/habedi/duckdb-micro/releases).**

---

### Benchmark Results

The table below shows execution times for a subset of the TPC-H queries running on the ESP32-P4 board.
Queries run against a persistent database stored on the microSD card (`/sdcard`).

| Query | Name                              | Operations                              | Scale 0.001 (~6,000 Rows) | Scale 0.01 (~60,000 Rows) |
|-------|-----------------------------------|-----------------------------------------|---------------------------|---------------------------|
| Q01   | Pricing Summary Report            | Scan, filter, aggregation, and grouping | 459 ms                    | 927 ms                    |
| Q03*  | Shipping Priority                 | Join, filter, aggregation, and sorting  | 655 ms                    | 930 ms                    |
| Q06   | Forecasting Revenue Change        | Scan, range filtering, and aggregation  | 274 ms                    | 450 ms                    |
| Q12   | Shipping Modes and Order Priority | Join, filtering, and aggregation        | 563 ms                    | 860 ms                    |
| Q14*  | Promotion Effect                  | Join and conditional aggregation        | 458 ms                    | 645 ms                    |

\* Evaluated with a session buffer limit of 12 MB (`--memory-limit-mb 12`).
Queries Q01, Q06, and Q12 run within the default 8 MB budget.

---

### Quickstart

To follow the steps below, you need to have Git and [Nix](https://nixos.org/download/) (with flakes enabled) installed on your machine.
The instructions are tested on Linux, and the firmware is tested on an ESP32-P4-WIFI6 board (silicon revision v1.3, 32 MB PSRAM, and 32 MB
NOR flash).

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

MicroShell is the name of the shell environment that runs on the board after flashing it.

#### 4. Flash the board

```shell
make flash TTY=/dev/ttyACM0
```

The board resets and starts the firmware after flashing.

#### 5. Connect to the board

```shell
make console TTY=/dev/ttyACM0
```

Press Enter after opening the console to get to the MicroShell (`msh>`) prompt.

[![Simple Demo 1](https://asciinema.org/a/DHMxhBJdkCUTsZAu.svg)](https://asciinema.org/a/DHMxhBJdkCUTsZAu)

[//]: # (<div align="center">)

[//]: # (  <img alt="MicroShell" src="docs/assets/images/msh-01.png" width="80%">)

[//]: # (</div>)

> [!NOTE]
> Serial port access needs membership in the `dialout` group (which can be set up with something like `sudo usermod -aG dialout $USER`).
> Also, change `/dev/ttyACM0` to the appropriate serial port for your system, if needed.

---

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to make a contribution.

### License

This project is licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)).
