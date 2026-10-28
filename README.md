## DuckDB Micro

An experimental DuckDB 2.0 port to ESP32-P4 (the Waveshare ESP32-P4-WIFI6).

See the [ROADMAP.md](ROADMAP.md) for the list of implemented and planned features.

> [!IMPORTANT]
> This project is in early development, so bugs and breaking changes are expected.
> Please use the [issues page](https://github.com/idea-playground/duckdb-micro/issues) to report bugs or request features.

---

### Quickstart

To follow the steps below, you need to have [Nix](https://nixos.org/download/) installed on your machine.
The target hardware is the Waveshare ESP32-P4-WIFI6 (silicon revision v1.3 with 32 MB PSRAM and 32 MB flash).

#### 1. Clone the repository and initialize submodules

```shell
git clone https://github.com/idea-playground/duckdb-micro.git
cd duckdb-micro
make submodules
```

#### 2. Set up the development environment

```shell
nix develop
make espidf-install
```

> [!NOTE]
> Serial port access needs membership in the `dialout` group (which can be set up with `sudo usermod -aG dialout $USER`).

#### 3. Build the firmware

```shell
make shell-build
```

#### 4. Flash and connect to the board

```shell
make flash TTY=/dev/ttyACM0
make console TTY=/dev/ttyACM0
```
---

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to make a contribution.

### License

This project is licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)).
