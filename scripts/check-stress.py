"""Exercise MicroShell repeatedly and record post-session heap fragmentation."""

import argparse
import csv
import importlib.util
from pathlib import Path
import re
import time
import uuid


spec = importlib.util.spec_from_file_location(
    "features", Path(__file__).with_name("check-features.py"))
features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(features)

TIERS = ("Internal SRAM", "SPI RAM (PSRAM)")


def memory_sample(output):
    sample = {}
    for tier in TIERS:
        match = re.search(rb"(?m)^" + re.escape(tier.encode()) +
                          rb"\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\r?$", output)
        if not match:
            raise RuntimeError(f"Missing memory metrics for {tier}")
        total, free, minimum, largest = map(int, match.groups())
        if not 0 < largest <= free <= total or not 0 <= minimum <= free:
            raise RuntimeError(f"Invalid memory metrics for {tier}")
        sample[tier] = dict(total=total, free=free, minimum=minimum, largest=largest)
    return sample


def check_memory(baseline, sample, streaks, max_loss_percent):
    for tier in TIERS:
        for metric in ("free", "largest"):
            key = (tier, metric)
            loss = 100 * (baseline[tier][metric] - sample[tier][metric]) / baseline[tier][metric]
            streaks[key] = streaks.get(key, 0) + 1 if loss > max_loss_percent else 0
            if streaks[key] >= 3:
                raise RuntimeError(f"{tier} {metric} fell {loss:.1f}% from warm-up baseline "
                                   "for 3 consecutive samples")


def command(port, capture, line, prompt=b"msh> ", expected=None, timeout=60):
    port.write(line.encode() + b"\r")
    # Linenoise redraws the prompt while echoing input; completed commands start a new line.
    output = features.wait_for(port, capture, b"\n" + prompt, timeout=timeout)
    if expected is not None and not re.search(
            rb"(?m)^" + re.escape(expected.encode()) + rb"\t\r?$", output):
        raise RuntimeError(f"Unexpected result for {line!r}; expected row {expected!r}")
    return output


def workload(port, capture, fixture, cycle):
    def sql(query, expected=None):
        return command(port, capture, query, b"duckdb> ", expected)

    command(port, capture, "duckdb", b"duckdb> ")
    sql("CREATE TABLE samples AS SELECT i, repeat('x', 32) AS payload "
        "FROM range(1000) t(i);", "1000")
    sql("SELECT count(*), sum(i) FROM samples;", "1000\t499500")
    sql("SELECT count(*), sum(a.i) FROM samples a JOIN samples b ON a.i = b.i;", "1000\t499500")
    sql("SELECT json_extract('{\"score\":99}', '$.score'), json_array_length('[1,2,3]');", "99\t3")
    sql(f"SELECT sum(score) FROM read_json('{fixture}.json');", "99")
    sql(f"COPY samples TO '{fixture}.parquet' (FORMAT PARQUET);", "1000")
    sql(f"SELECT count(*), sum(i) FROM read_parquet('{fixture}.parquet');", "1000\t499500")
    command(port, capture, ".quit")
    command(port, capture, f"duckdb {fixture}.duckdb", b"duckdb> ")
    sql("CREATE TABLE IF NOT EXISTS cycles (id BIGINT);")
    sql("BEGIN TRANSACTION;")
    sql(f"INSERT INTO cycles VALUES ({cycle});", "1")
    sql("COMMIT;")
    command(port, capture, ".quit")
    command(port, capture, f"duckdb {fixture}.duckdb", b"duckdb> ")
    sql("SELECT count(*), sum(id) FROM cycles;", f"{cycle}\t{cycle * (cycle + 1) // 2}")
    command(port, capture, ".quit")


def run(port, capture, report, args, fixture):
    command(port, capture, f'write {fixture}.json {{"score":99}}')
    writer = csv.writer(report)
    writer.writerow(("cycle", "phase", "elapsed_seconds", "cycle_seconds", "tier", "total_bytes",
                     "free_bytes", "minimum_free_bytes", "largest_block_bytes",
                     "fragmentation_percent"))
    baseline = None
    streaks = {}
    started = time.monotonic()
    measured_start = None
    cycle = 0
    while True:
        cycle += 1
        cycle_start = time.monotonic()
        workload(port, capture, fixture, cycle)
        sample = memory_sample(command(port, capture, "free"))
        now = time.monotonic()
        phase = "warmup" if cycle <= args.warmup else "measured"
        for tier, metrics in sample.items():
            fragmentation = 100 * (1 - metrics["largest"] / metrics["free"])
            writer.writerow((cycle, phase, f"{now - started:.3f}", f"{now - cycle_start:.3f}",
                             tier, *metrics.values(), f"{fragmentation:.3f}"))
        report.flush()
        print(f"Cycle {cycle} ({phase}): {now - cycle_start:.1f}s; " + "; ".join(
            f"{tier}: free={m['free']}, largest={m['largest']}"
            for tier, m in sample.items()), flush=True)
        if cycle == args.warmup:
            baseline = sample
            measured_start = now
        elif cycle > args.warmup:
            check_memory(baseline, sample, streaks, args.max_loss_percent)
            if args.duration is not None:
                if cycle - args.warmup >= 3 and now - measured_start >= args.duration:
                    break
            elif cycle - args.warmup >= args.cycles:
                break
    for suffix in (".json", ".parquet", ".duckdb"):
        path = fixture + suffix
        output = command(port, capture, f"rm {path}")
        if b"rm:" in output:
            raise RuntimeError(f"Could not remove {path}")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tty")
    parser.add_argument("--storage", choices=("/sdcard", "/data"), default="/sdcard")
    parser.add_argument("--cycles", type=int, default=20, help="measured cycles (default: 20)")
    parser.add_argument("--duration", type=float, help="measured seconds, instead of --cycles")
    parser.add_argument("--warmup", type=int, default=3, help="warm-up cycles (default: 3)")
    parser.add_argument("--max-loss-percent", type=float, default=5,
                        help="allowed free/largest-block loss from baseline (default: 5)")
    args = parser.parse_args(argv)
    if args.cycles < 3 or args.warmup < 1:
        parser.error("--cycles must be at least 3 and --warmup must be positive")
    if args.duration is not None and not 0 < args.duration < float("inf"):
        parser.error("--duration must be finite and positive")
    if not 0 < args.max_loss_percent < 100:
        parser.error("--max-loss-percent must be between 0 and 100")
    return args


def main():
    args = arguments()
    import serial
    run_id = uuid.uuid4().hex
    fixture = f"{args.storage}/duckdb-stress-{run_id}"
    features.log = Path(__file__).resolve().parents[1] / f"build/hardware/stress-{run_id}.log"
    features.log.parent.mkdir(parents=True, exist_ok=True)
    report_path = features.log.with_suffix(".csv")
    print(f"Serial log: {features.log}\nMemory report: {report_path}\n"
          f"Fixture prefix: {fixture}", flush=True)
    try:
        with serial.Serial(port=None, baudrate=115200, timeout=0.2, exclusive=True) as port, \
                features.log.open("wb") as capture, report_path.open("w", newline="") as report:
            port.port = args.tty
            port.dtr = False
            port.rts = False
            port.open()
            port.rts = True
            time.sleep(0.1)
            port.rts = False
            features.pending = b""
            features.wait_for(port, capture, b"msh> ", timeout=35)
            features.fatal += (b"ESP-ROM:", b"duckdb:", b"ALLOC_FAIL", b"write:")
            run(port, capture, report, args, fixture)
        print("PASS: stress workloads, persistence, and post-session memory checks")
    except (RuntimeError, OSError, serial.SerialException) as error:
        raise SystemExit(f"{error}\nSee {features.log} and {report_path}. "
                         f"Failed-run files retained under {fixture}*") from error


if __name__ == "__main__":
    main()
