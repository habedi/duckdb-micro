"""Prepare and check a small TPC-H workload without adding firmware extensions."""

import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
QUERIES = (1, 6, 3, 12, 14)
DEFAULT_QUERIES = (1, 6, 12)
spec = importlib.util.spec_from_file_location("stress", Path(__file__).with_name("check-stress.py"))
stress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stress)


def query_sql(number):
    path = ROOT / f"external/duckdb/extension/tpch/dbgen/queries/q{number:02}.sql"
    return " ".join(path.read_text().split()).rstrip(";")


def host_query(cli, database, sql, readonly=True):
    command = [cli, "-batch", "-bail", "-json"]
    if readonly:
        command.append("-readonly")
    output = subprocess.run([*command, str(database)], input=sql, text=True,
                            capture_output=True, check=True).stdout
    return json.loads(output) if output.strip() else []


def prepare(args):
    # A fresh directory prevents overwriting an existing benchmark or user database.
    args.output.mkdir(parents=True, exist_ok=False)
    database = args.output / "tpch.duckdb"
    version = host_query(args.duckdb, ":memory:", "SELECT * FROM pragma_version();", False)[0]
    host_query(args.duckdb, database,
               f"SET threads=1; LOAD tpch; CALL dbgen(sf={args.scale});", False)
    queries = []
    for number in QUERIES:
        sql = query_sql(number)
        rows = host_query(args.duckdb, database,
                          f"SET threads=1; SELECT COLUMNS(*)::VARCHAR FROM ({sql}) q;")
        queries.append(dict(number=number, sql=sql,
                            rows=[list(row.values()) for row in rows]))
    with database.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    manifest = dict(scale=args.scale, host_version=version,
                    database_sha256=digest, queries=queries)
    path = args.output / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared {database} and {path}\n"
          f"Then run: python scripts/check-tpch.py run /dev/ttyACM0 {path}")


def insertion_batches(table, rows):
    identifier = '"' + table.replace('"', '""') + '"'
    prefix = f"INSERT INTO {identifier} VALUES "
    values = []
    for row in rows:
        value = "(" + ",".join("NULL" if cell is None else "'" + cell.replace("'", "''") + "'"
                               for cell in row.values()) + ")"
        if re.search(r"[\x00-\x1f\x7f]", value) or len((prefix + value + ";").encode()) >= 1000:
            raise RuntimeError(f"Row cannot fit on a serial command line in {table}")
        if values and len((prefix + ",".join([*values, value]) + ";").encode()) >= 900:
            yield prefix + ",".join(values) + ";", len(values)
            values = []
        values.append(value)
    if values:
        yield prefix + ",".join(values) + ";", len(values)


def send_command(port, capture, line, prompt=b"msh> ", expected=None, timeout=60):
    data = line.encode()
    # Long pastes can overrun the UART receive buffer while linenoise echoes input.
    while len(data) > 64:
        port.write(data[:64])
        data = data[64:]
        time.sleep(0.02)
    return stress.command(port, capture, data.decode(), prompt, expected, timeout)


def load_fixture(port, capture, args):
    def command(line, expected=None):
        return send_command(port, capture, line, b"duckdb> ", expected, timeout=args.timeout)

    listing = send_command(port, capture, "ls /sdcard")
    if Path(args.database).name.encode() in listing:
        raise RuntimeError("Generated database path already exists; refusing to overwrite it")
    command(f"duckdb {args.database}")
    source = args.manifest.with_name("tpch.duckdb")
    tables = host_query(args.duckdb, source,
                        "SELECT table_name, sql FROM duckdb_tables() ORDER BY table_name;")
    for table in tables:
        name = table["table_name"]
        identifier = '"' + name.replace('"', '""') + '"'
        rows = host_query(args.duckdb, source, f"SELECT COLUMNS(*)::VARCHAR FROM {identifier};")
        command(table["sql"])
        command("BEGIN TRANSACTION;")
        loaded = 0
        for index, (sql, count) in enumerate(insertion_batches(name, rows)):
            command(sql, str(count))
            loaded += count
            # Bound transaction and session state within the board's internal SRAM budget.
            if (index + 1) % 20 == 0:
                command("COMMIT;")
                send_command(port, capture, ".quit")
                command(f"duckdb {args.database}")
                command("BEGIN TRANSACTION;")
            if index % 100 == 0:
                print(f"Loading {name}: {loaded}/{len(rows)} rows", flush=True)
        command("COMMIT;")
        command(f"SELECT count(*) FROM {identifier};", str(len(rows)))
        print(f"Loaded {name}: {loaded} rows, count verified", flush=True)
    command("CHECKPOINT;")
    send_command(port, capture, ".quit")


def result_rows(output):
    summary = re.search(rb"(?m)^(\d+) row\(s\), (\d+) ms\r?$", output)
    if not summary:
        raise RuntimeError("Missing complete query result or firmware timing")
    count, milliseconds = map(int, summary.groups())
    lines = output[:summary.start()].decode().splitlines()
    if len(lines) < count + 1:
        raise RuntimeError("Incomplete query rows")
    rows = [line.removesuffix("\t").split("\t") for line in lines[-count:]] if count else []
    return rows, milliseconds


def check_rows(actual, expected):
    if len(actual) != len(expected):
        raise RuntimeError(f"Row count mismatch: {len(actual)} != {len(expected)}")
    for row, reference in zip(actual, expected):
        if len(row) != len(reference):
            raise RuntimeError("Column count mismatch")
        for value, wanted in zip(row, reference):
            if value == ("NULL" if wanted is None else wanted):
                continue
            # Floating aggregates can differ slightly between host and RISC-V.
            try:
                number, target = Decimal(value), Decimal(wanted)
                if number.is_finite() and target.is_finite() and abs(number - target) <= max(
                        Decimal("1e-8"), abs(target) * Decimal("1e-9")):
                    continue
            except (InvalidOperation, TypeError):
                pass
            raise RuntimeError(f"Result mismatch: {value!r} != {wanted!r}")


def run_queries(port, capture, report, manifest, args):
    def command(line, prompt=b"duckdb> "):
        return send_command(port, capture, line, prompt, timeout=args.timeout)

    writer = csv.writer(report)
    writer.writerow(("query", "phase", "iteration", "query_ms", "tier", "free_bytes",
                     "minimum_free_bytes", "largest_block_bytes"))
    for query in manifest["queries"]:
        if query["number"] not in args.queries:
            continue
        for iteration in range(args.repeats + 1):
            command("duckdb", b"duckdb> ")
            command(f"SET memory_limit='{args.memory_limit_mb}MB';")
            command(f"ATTACH '{args.database}' AS tpch (READ_ONLY);")
            command("USE tpch;")
            output = command(query["sql"] + ";")
            rows, elapsed = result_rows(output)
            check_rows(rows, query["rows"])
            # Sample after releasing the session, so retained result buffers do not look like leaks.
            command(".quit", b"msh> ")
            sample = stress.memory_sample(command("free", b"msh> "))
            phase = "warmup" if iteration == 0 else "measured"
            for tier, metrics in sample.items():
                writer.writerow((query["number"], phase, iteration, elapsed, tier,
                                 metrics["free"], metrics["minimum"], metrics["largest"]))
            report.flush()
            print(f"Q{query['number']:02} {phase} {iteration}: {elapsed} ms, results match",
                  flush=True)


def run(args):
    import serial

    if not args.manifest.exists():
        prepare(argparse.Namespace(output=args.manifest.parent, duckdb=args.duckdb,
                                   scale=args.scale or 0.001))
    manifest = json.loads(args.manifest.read_text())
    if args.scale is not None and args.scale != manifest["scale"]:
        raise RuntimeError("Requested scale differs from the manifest; "
                           "use a separate fixture directory")
    if [q["number"] for q in manifest["queries"]] != list(QUERIES) or any(
            q["sql"] != query_sql(q["number"]) for q in manifest["queries"]):
        raise RuntimeError("Manifest queries differ from the pinned subset; prepare a new fixture")
    run_id = uuid.uuid4().hex
    generated = args.database is None
    if generated:
        args.database = f"/sdcard/tpch-{run_id}.duckdb"
        with args.manifest.with_name("tpch.duckdb").open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != manifest["database_sha256"]:
                raise RuntimeError("Host database digest differs from the manifest")
    features = stress.features
    features.log = ROOT / f"build/hardware/tpch-{run_id}.log"
    features.log.parent.mkdir(parents=True, exist_ok=True)
    report_path = features.log.with_suffix(".csv")
    features.log.with_suffix(".json").write_text(json.dumps(dict(
        manifest=manifest, database=args.database, generated_database=generated,
        repeats=args.repeats, queries=args.queries, memory_limit_mb=args.memory_limit_mb,
        timeout=args.timeout), indent=2) + "\n")
    print(f"Serial log: {features.log}\nReport: {report_path}", flush=True)
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
        features.fatal += (b"ESP-ROM:", b"duckdb:", b"ALLOC_FAIL")
        if generated:
            load_fixture(port, capture, args)
        run_queries(port, capture, report, manifest, args)
        if generated:
            output = send_command(port, capture, f"rm {args.database}")
            if b"rm:" in output:
                raise RuntimeError(f"Could not remove generated database {args.database}")
    print("PASS: TPC-H subset results match host DuckDB")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prep = commands.add_parser("prepare", help="generate host database and expected results")
    prep.add_argument("--duckdb", default="duckdb", help="host DuckDB CLI with tpch installed")
    prep.add_argument("--scale", type=float, choices=(0.001, 0.01), default=0.001)
    prep.add_argument("--output", type=Path, default=ROOT / "build/tpch")
    hardware = commands.add_parser("run", help="prepare, load, and benchmark the SD-card database")
    hardware.add_argument("tty")
    hardware.add_argument("manifest", type=Path, nargs="?",
                          default=ROOT / "build/tpch/manifest.json")
    hardware.add_argument("--duckdb", default="duckdb", help="host DuckDB CLI with tpch installed")
    hardware.add_argument("--scale", type=float, choices=(0.001, 0.01))
    hardware.add_argument("--memory-limit-mb", type=int, choices=(8, 12), default=8,
                          help="query buffer limit in MB; firmware default stays unchanged")
    hardware.add_argument("--queries", type=int, nargs="+", choices=QUERIES,
                          default=DEFAULT_QUERIES, help="query numbers (default: 1 6 12)")
    hardware.add_argument("--database",
                          help="use an existing /sdcard/NAME.duckdb without loading it")
    hardware.add_argument("--repeats", type=int, default=3, help="measured runs per query")
    hardware.add_argument("--timeout", type=float, default=120, help="seconds per command")
    args = parser.parse_args(argv)
    if args.action == "run":
        if args.database is not None and not re.fullmatch(
                r"/sdcard/[A-Za-z0-9_-]+\.duckdb", args.database):
            parser.error("--database must be /sdcard/NAME.duckdb, using letters, digits, _ or -")
        if args.repeats < 1 or not 0 < args.timeout < float("inf"):
            parser.error("--repeats must be positive and --timeout finite and positive")
    return args


def main():
    args = arguments()
    try:
        if args.action == "prepare":
            prepare(args)
        else:
            run(args)
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise SystemExit(detail) from error


if __name__ == "__main__":
    main()
