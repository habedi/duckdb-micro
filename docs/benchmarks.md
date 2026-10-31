## Benchmark Results

The tables below show execution times for a subset of TPC-H queries running directly on the ESP32-P4 board.
All queries run against a persistent database stored on the microSD card (`/sdcard`).

### Hardware Execution Times

| Query | Name                              | Scale 0.001 (~6,000 Rows) | Scale 0.01 (~60,000 Rows) |
|-------|-----------------------------------|---------------------------|---------------------------|
| Q01   | Pricing Summary Report            | 459 ms                    | 927 ms                    |
| Q03*  | Shipping Priority                 | 655 ms                    | 930 ms                    |
| Q06   | Forecasting Revenue Change        | 274 ms                    | 450 ms                    |
| Q12   | Shipping Modes and Order Priority | 563 ms                    | 860 ms                    |
| Q14*  | Promotion Effect                  | 458 ms                    | 645 ms                    |

\* Evaluated with a session buffer limit of 12 MB (`--memory-limit-mb 12`).
Queries Q01, Q06, and Q12 run within the default 8 MB budget.

### Host Comparison (Single-Threaded)

The tables below compare the ESP32-P4 board against a desktop host running DuckDB in single-threaded mode (`SET threads=1;`).

- **ESP32-P4 Board:** Waveshare ESP32-P4-WIFI6 (dual-core 32-bit RISC-V at 400 MHz, 32 MB PSRAM, microSD storage).
- **Host PC:** AMD Ryzen 9 9950X (16 cores, up to 5.7 GHz, DDR5 RAM, NVMe and page cache storage).

#### Scale Factor 0.001 (~6,000 Rows)

| Query | Name                              | ESP32-P4 (1 Thread) | Host PC (1 Thread) | Ratio   |
|-------|-----------------------------------|---------------------|--------------------|---------|
| Q01   | Pricing Summary Report            | 459 ms              | 0.59 ms            | ~780x   |
| Q03*  | Shipping Priority                 | 655 ms              | 0.79 ms            | ~830x   |
| Q06   | Forecasting Revenue Change        | 274 ms              | 0.22 ms            | ~1,250x |
| Q12   | Shipping Modes and Order Priority | 563 ms              | 0.71 ms            | ~790x   |
| Q14*  | Promotion Effect                  | 458 ms              | 0.47 ms            | ~970x   |

#### Scale Factor 0.01 (~60,000 Rows)

| Query | Name                              | ESP32-P4 (1 Thread) | Host PC (1 Thread) | Ratio   |
|-------|-----------------------------------|---------------------|--------------------|---------|
| Q01   | Pricing Summary Report            | 927 ms              | 1.24 ms            | ~750x   |
| Q03*  | Shipping Priority                 | 930 ms              | 1.17 ms            | ~795x   |
| Q06   | Forecasting Revenue Change        | 450 ms              | 0.33 ms            | ~1,360x |
| Q12   | Shipping Modes and Order Priority | 860 ms              | 0.99 ms            | ~870x   |
| Q14*  | Promotion Effect                  | 645 ms              | 0.65 ms            | ~990x   |

\* Evaluated on the ESP32-P4 with a 12 MB buffer limit (`--memory-limit-mb 12`).

### Running the Benchmarks

1. Generate the test dataset on the host
   ```shell
   make prepare-tpch
   ```
2. Run the hardware benchmark suite
   ```shell
   make check-tpch TTY=/dev/ttyACM0
   ```
3. Generate a Markdown summary from the serial run
   ```shell
   make report-tpch
   ```
