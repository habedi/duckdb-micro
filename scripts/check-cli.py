"""Check the upstream CLI, its file commands, and repeated-session heap usage."""

import importlib.util
from pathlib import Path
import re
import sys
import time
import uuid


spec = importlib.util.spec_from_file_location("tpch", Path(__file__).with_name("check-tpch.py"))
tpch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tpch)
stress = tpch.stress


def main():
    import serial

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/check-cli.py /dev/ttyACM0")
    prefix = "/sdcard/cli-" + uuid.uuid4().hex
    features = stress.features
    features.log = Path(__file__).resolve().parents[1] / ("build/hardware/" + Path(prefix).name + ".log")
    features.log.parent.mkdir(parents=True, exist_ok=True)
    print(f"Serial log: {features.log}", flush=True)
    with serial.Serial(port=None, baudrate=115200, timeout=0.2, exclusive=True) as port, \
            features.log.open("wb") as capture:
        port.port = sys.argv[1]
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
        baseline = None
        streaks = {}
        for session in range(5):
            command("duckdb --cli")
            command("SELECT", prompt=b"   ...> ")
            command("6 * 7 AS answer;", b"42")
            command("SELECT 6 * 7; SELECT 7 * 7;", b"49")
            if session == 0:
                command(".last", b"49")
                command("CREATE TABLE cli_data AS SELECT * FROM (VALUES (1), (2), (3)) t(x);")
                command(".tables", b"cli_data")
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
                command(".mode trash")
                command("SELECT 6 * 7;")
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
                command("SELECT", prompt=b"   ...> ")
                time.sleep(0.1)
                port.write(b"\x03")
                features.wait_for(port, capture, b"\nduckdb> ")
                command("SELECT 6 * 7;", b"42")
                command(f".open {prefix}.duckdb")
                command("CREATE TABLE persisted AS SELECT 6 * 7 AS x;")
                command("CHECKPOINT;")
            command(".exit", prompt=b"msh> ", allow_error=True)
            if session == 0:
                command(f"cat {prefix}.out", b"cli_value", prompt=b"msh> ")
            command(f"duckdb --cli {prefix}.duckdb")
            command("SELECT sum(x) FROM persisted;", b"42")
            command(".quit", prompt=b"msh> ")
            sample = stress.memory_sample(command("free", prompt=b"msh> "))
            if baseline is None:
                baseline = sample
            else:
                stress.check_memory(baseline, sample, streaks, 5)
            print(f"Session {session + 1}: correct results, reopen, and cleanup; "
                  f"internal free={sample['Internal SRAM']['free']}", flush=True)
        for suffix in (".csv", ".sql", ".loop.sql", ".out", ".duckdb"):
            command("rm " + prefix + suffix, prompt=b"msh> ")
    print("PASS: upstream CLI modes, multiline SQL, file commands, and repeated sessions")


if __name__ == "__main__":
    main()
