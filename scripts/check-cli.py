"""Check the upstream CLI, its file commands, and repeated-session heap usage."""

import argparse
import csv
import importlib.util
from pathlib import Path
import re
import time
import uuid


spec = importlib.util.spec_from_file_location("tpch", Path(__file__).with_name("check-tpch.py"))
tpch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tpch)
stress = tpch.stress


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tty")
    parser.add_argument("--duration", type=float,
                        help="repeat mixed sessions for this many seconds, including warm-up")
    args = parser.parse_args(argv)
    if args.duration is not None and not 0 < args.duration < float("inf"):
        parser.error("--duration must be finite and positive")
    return args


def main():
    args = arguments()
    import serial
    prefix = "/sdcard/cli-" + uuid.uuid4().hex
    features = stress.features
    features.log = Path(__file__).resolve().parents[1] / ("build/hardware/" + Path(prefix).name + ".log")
    features.log.parent.mkdir(parents=True, exist_ok=True)
    print(f"Serial log: {features.log}", flush=True)
    with serial.Serial(port=None, baudrate=115200, timeout=0.2, exclusive=True) as port, \
            features.log.open("wb") as capture, \
            features.log.with_suffix(".csv").open("w", newline="") as report:
        port.port = args.tty
        port.dtr = False
        port.rts = False
        port.open()
        port.rts = True
        time.sleep(0.1)
        port.rts = False
        features.wait_for(port, capture, b"msh> ", timeout=35)

        def command(line, needle=None, prompt=b"duckdb> ", allow_error=False):
            output = tpch.send_command(port, capture, line, prompt, timeout=90)
            if not allow_error and re.search(rb"(?:Error:|error:|Exception:|ALLOC_FAIL)", output):
                raise RuntimeError(f"CLI error for {line!r}: {output!r}")
            if needle is not None:
                if needle in line.encode():
                    raise AssertionError("A result marker must not match the echoed command")
                if needle not in output:
                    raise RuntimeError(f"Missing result {needle!r} for {line!r}: {output!r}")
            return output

        command(f'write {prefix}.csv "6,7"', prompt=b"msh> ")
        command(f'write {prefix}.sql "CREATE TABLE script_data AS SELECT 6 * 7 AS x;"',
                prompt=b"msh> ")
        command(f'write {prefix}.loop.sql ".read {prefix}.loop.sql"', prompt=b"msh> ")
        command('duckdb --cli -c "SELECT 42"', b"omit -c", prompt=b"msh> ", allow_error=True)
        baseline = None
        streaks = {}
        writer = csv.writer(report)
        writer.writerow(("session", "elapsed_seconds", "tier", "free_bytes", "largest_block_bytes"))
        started = time.monotonic()
        session = 0
        while True:
            command("duckdb" if session == 0 else "duckdb --cli")
            command("SELECT", prompt=b"   ...> ")
            command("6 * 7 AS answer;", b"42")
            command("SELECT 6 * 7; SELECT 7 * 7;", b"49")
            if session == 0:
                command(".last", b"49")
                command("CREATE TABLE cli_data AS SELECT * FROM (VALUES (1), (2), (3)) t(x);")
                command(".tables", b"cli_data")
                time.sleep(0.1)
                port.write(b".tabl")
                time.sleep(0.1)
                port.write(b"\t")
                command("", b"cli_data")
                time.sleep(0.1)
                port.write(b"SEL")
                time.sleep(0.1)
                port.write(b"\t")
                command(" 6 * 7 AS completed;", b"42")
                time.sleep(0.1)
                port.write(b"\x1b[A")
                command("", b"42")
                command('.prompt "cli> " "next> "', prompt=b"cli> ")
                command("SELECT", prompt=b"next> ")
                command("6 * 7;", b"42", prompt=b"cli> ")
                command('.prompt "duckdb> " "   ...> "')
                command("SELECT 6 * 7;", b"42")
                time.sleep(0.1)
                port.write(b"SELECT * FROM cli_d")
                time.sleep(0.1)
                port.write(b"\t")
                time.sleep(0.1)
                port.write(b"\x03")
                features.wait_for(port, capture, b"\nduckdb> ")
                command(".last", b"42")
                command(".schema cli_data", b"CREATE TABLE")
                command("CREATE INDEX cli_index ON cli_data(x);")
                command(".indexes", b"cli_index")
                command(".databases")
                command(".manual abs")
                command(".help")
                for mode in ("duckbox", "table", "box", "column", "csv", "list", "tabs", "markdown",
                             "json", "jsonlines", "line", "quote", "html", "latex", "tcl", "ascii",
                             "insert cli_copy"):
                    command(".mode " + mode)
                    command("SELECT 6 * 7 AS id, concat('cli_', 'value') AS label;", b"cli_value")
                    if mode == "json":
                        command("SELECT 'a' || chr(0) || 'b';", b"a\\u0000b")
                command(".mode trash")
                command("SELECT 6 * 7;")
                command(".mode csv")
                command("SELECT repeat('x', 2100000);", b"CLI rendering exceeds", allow_error=True)
                command(".mode tabs")
                command(".headers off")
                command(f".read {prefix}.sql")
                command("SELECT sum(x) FROM script_data;", b"42")
                command(f".read {prefix}.loop.sql", b"recursive", allow_error=True)
                command("SELECT 6 * 7;", b"42")
                command(f".import --csv --header false {prefix}.csv imported")
                command("SELECT sum(column0 + column1) FROM imported;", b"13")
                command(".timer on")
                command("SELECT 6 * 7;", b"Run Time")
                command(".timer off")
                command(f".output {prefix}.out")
                command("SELECT concat('cli_', 'value');")
                command(".output")
                command(".mode list")
                command(f".output {prefix}.large.sql")
                command("SELECT repeat('x', 20000);")
                command(".output")
                command(f".read {prefix}.large.sql", b"exceeds 16 KiB", allow_error=True)
                command("SELECT 6 * 7;", b"42")
                command("SELECT", prompt=b"   ...> ")
                time.sleep(0.1)
                port.write(b"\x03")
                features.wait_for(port, capture, b"\nduckdb> ")
                command("SELECT 6 * 7;", b"42")
                command(f".open {prefix}.duckdb")
                command("CREATE TABLE persisted AS SELECT 6 * 7 AS x;")
                command("CHECKPOINT;")
            if session > 0:
                command("CREATE TABLE samples AS SELECT i FROM range(1000) t(i);")
                command(".mode tabs")
                command(".headers off")
                command("SELECT count(*), sum(a.i) FROM samples a JOIN samples b USING(i);",
                        b"1000\t499500")
                command(f".import --csv --header false {prefix}.csv imported")
                command("SELECT sum(column0 + column1) FROM imported;", b"13")
                for mode in ("duckbox", "csv", "json", "markdown"):
                    command(".mode " + mode)
                    command("SELECT 6 * 7 AS answer;", b"42")
                command(f".read {prefix}.sql")
                command("SELECT sum(x) FROM script_data;", b"42")
            command(".log stdout")
            command(".exit", prompt=b"msh> ", allow_error=True)
            if session == 0:
                command(f"cat {prefix}.out", b"cli_value", prompt=b"msh> ")
            command(f"duckdb {prefix}.duckdb")
            command("SELECT sum(x) FROM persisted;", b"42")
            command(".quit", prompt=b"msh> ")
            sample = stress.memory_sample(command("free", prompt=b"msh> "))
            if baseline is None:
                baseline = sample
            else:
                stress.check_memory(baseline, sample, streaks, 5)
            elapsed = time.monotonic() - started
            for tier, metrics in sample.items():
                writer.writerow((session + 1, f"{elapsed:.3f}", tier,
                                 metrics["free"], metrics["largest"]))
            report.flush()
            print(f"Session {session + 1} ({elapsed:.1f}s): correct results, reopen, and cleanup; "
                  f"internal free={sample['Internal SRAM']['free']}", flush=True)
            session += 1
            if (args.duration is None and session >= 5) or (
                    args.duration is not None and elapsed >= args.duration):
                break
        command('duckdb -c "SELECT count(*) FROM duckdb_tables;"', b"\n0\t", prompt=b"msh> ")
        for suffix in (".csv", ".sql", ".loop.sql", ".large.sql", ".out", ".duckdb"):
            output = command("rm " + prefix + suffix, prompt=b"msh> ")
            if b"rm:" in output:
                raise RuntimeError(f"Could not remove {prefix + suffix}")
        listing = command("ls /sdcard", prompt=b"msh> ")
        if Path(prefix).name.encode() in listing:
            raise RuntimeError(f"Test files remain under {prefix}")
    print("PASS: upstream CLI modes, multiline SQL, file commands, and repeated sessions")


if __name__ == "__main__":
    main()
