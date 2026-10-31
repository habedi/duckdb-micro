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
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
QUERIES = (1, 6, 3, 12, 14)
DEFAULT_QUERIES = (1, 6, 12)
QUERY_TITLES = {
    1: "Pricing Summary Report",
    2: "Minimum Cost Supplier",
    3: "Shipping Priority",
    4: "Order Priority Checking",
    5: "Local Supplier Volume",
    6: "Forecasting Revenue Change",
    7: "Volume Shipping",
    8: "National Market Share",
    9: "Product Type Measure",
    10: "Returned Item Reporting",
    11: "Important Stock Identification",
    12: "Shipping Modes and Order Priority",
    13: "Customer Distribution",
    14: "Promotion Effect",
    15: "Top Supplier",
    16: "Parts/Supplier Relationship",
    17: "Small-Quantity-Order Revenue",
    18: "Large Volume Customer",
    19: "Discounted Revenue",
    20: "Potential Part Promotion",
    21: "Suppliers Who Kept Orders Waiting",
    22: "Global Sales Opportunity",
}
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
    command(f"duckdb --compact {args.database}")
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
                command(f"duckdb --compact {args.database}")
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
            command("duckdb --compact", b"duckdb> ")
            command(f"SET threads={args.threads};")
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
        threads=args.threads,
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
    if report_path.is_file() and report_path.stat().st_size > 0:
        try:
            md_path = report_path.with_suffix(".md")
            md_path.write_text(
                generate_markdown_report(report_path, report_path.with_suffix(".json")),
                encoding="utf-8")
            print(f"Markdown report: {md_path}")
        except Exception:
            pass


def format_bytes(count):
    if count is None:
        return "N/A"
    if count >= 1024 * 1024:
        return f"{count / (1024 * 1024):.2f} MB"
    if count >= 1024:
        return f"{count / 1024:.1f} KB"
    return f"{count} B"


def format_bytes_detail(count):
    if count is None:
        return "N/A"
    return f"{count:,} B ({format_bytes(count)})"


def parse_report_data(csv_path, json_path=None):
    csv_file = Path(csv_path)
    if not csv_file.is_file():
        raise RuntimeError(f"Report CSV file not found: {csv_file}")

    with csv_file.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    if not rows:
        raise RuntimeError(f"Report CSV file has no data rows: {csv_file}")

    meta = {}
    if json_path is not None:
        json_file = Path(json_path)
        if json_file.is_file():
            try:
                meta = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}

    queries_data = {}
    for row in rows:
        q_num = int(row["query"])
        phase = row["phase"]
        iteration = int(row["iteration"])
        elapsed = int(row["query_ms"])
        tier = row["tier"]
        free_bytes = int(row["free_bytes"]) if row.get("free_bytes") else 0
        min_free = int(row["minimum_free_bytes"]) if row.get("minimum_free_bytes") else None
        largest_block = int(row["largest_block_bytes"]) if row.get("largest_block_bytes") else None

        if q_num not in queries_data:
            queries_data[q_num] = {
                "number": q_num,
                "warmup_ms": None,
                "measured_ms": [],
                "iterations": [],
            }

        q_info = queries_data[q_num]
        iter_entry = next((item for item in q_info["iterations"]
                           if item["phase"] == phase and item["iteration"] == iteration), None)
        if iter_entry is None:
            iter_entry = {
                "phase": phase,
                "iteration": iteration,
                "elapsed": elapsed,
                "memory": {},
            }
            q_info["iterations"].append(iter_entry)
            if phase == "warmup":
                q_info["warmup_ms"] = elapsed
            elif phase == "measured":
                q_info["measured_ms"].append(elapsed)

        iter_entry["memory"][tier] = {
            "free": free_bytes,
            "minimum": min_free,
            "largest": largest_block,
        }

    return queries_data, meta


def generate_markdown_report(csv_path, json_path=None):
    queries_data, meta = parse_report_data(csv_path, json_path)
    manifest = meta.get("manifest", {})
    manifest_queries = {q["number"]: q for q in manifest.get("queries", [])}
    host_ver = manifest.get("host_version", {})
    host_ver_str = host_ver.get("library_version", "")
    if host_ver.get("codename"):
        host_ver_str += f" ({host_ver['codename']})"

    lines = [
        "# TPC-H Benchmark Report",
        "",
        "This report summarizes DuckDB 2.0 TPC-H subset benchmark results on the "
        "ESP32-P4 microcontroller.",
        "",
        "## Benchmark Environment",
        "",
        "| Property | Value |",
        "|---|---|",
        "| Target Hardware | Waveshare ESP32-P4-WIFI6 (32 MB PSRAM, 32 MB Flash) |",
    ]

    scale = manifest.get("scale", meta.get("scale"))
    if scale is not None:
        lines.append(f"| Scale Factor | {scale} |")
    if "memory_limit_mb" in meta:
        lines.append(f"| Buffer Memory Limit | {meta['memory_limit_mb']} MB |")
    if "threads" in meta:
        lines.append(f"| Query Threads | {meta['threads']} |")
    if "repeats" in meta:
        lines.append(f"| Measured Iterations | {meta['repeats']} |")
    if "database" in meta:
        lines.append(f"| Database Path | `{meta['database']}` |")
    if host_ver_str:
        lines.append(f"| Host DuckDB Version | {host_ver_str} |")
    if manifest.get("database_sha256"):
        lines.append(f"| Database SHA-256 | `{manifest['database_sha256']}` |")
    lines.append(f"| Benchmark Run Source | `{Path(csv_path).name}` |")
    lines.append("")

    lines.extend([
        "## Performance Summary",
        "",
        "| Query | Description | Warmup (ms) | Min (ms) | Avg (ms) | Max (ms) |",
        "|---|---|---|---|---|---|",
    ])

    total_warmup = 0
    total_min = 0
    total_avg = 0.0
    total_max = 0
    has_warmup = False

    for q_num, info in sorted(queries_data.items()):
        name = QUERY_TITLES.get(q_num, f"Query {q_num}")
        warmup = info["warmup_ms"]
        warmup_str = f"{warmup:,}" if warmup is not None else "N/A"
        if warmup is not None:
            total_warmup += warmup
            has_warmup = True

        measured = info["measured_ms"]
        if measured:
            q_min = min(measured)
            q_avg = sum(measured) / len(measured)
            q_max = max(measured)
            total_min += q_min
            total_avg += q_avg
            total_max += q_max
            lines.append(f"| Q{q_num:02} | {name} | {warmup_str} | {q_min:,} | "
                         f"{q_avg:,.1f} | {q_max:,} |")
        else:
            lines.append(f"| Q{q_num:02} | {name} | {warmup_str} | N/A | N/A | N/A |")

    tot_warm_str = f"{total_warmup:,}" if has_warmup else "N/A"
    lines.append(f"| **Total** | | **{tot_warm_str}** | **{total_min:,}** | "
                 f"**{total_avg:,.1f}** | **{total_max:,}** |")
    lines.append("")

    has_min_free = any(
        it["memory"].get("Internal SRAM", {}).get("minimum") is not None
        for info in queries_data.values() for it in info["iterations"]
    )

    lines.extend([
        "## Memory Statistics",
        "",
    ])
    if has_min_free:
        lines.extend([
            "| Query | Description | SRAM Low Watermark | SRAM Final Free | "
            "PSRAM Low Watermark | PSRAM Final Free |",
            "|---|---|---|---|---|---|",
        ])
        for q_num, info in sorted(queries_data.items()):
            name = QUERY_TITLES.get(q_num, f"Query {q_num}")
            sram_mins = [it["memory"]["Internal SRAM"]["minimum"] for it in info["iterations"]
                         if it["memory"].get("Internal SRAM", {}).get("minimum") is not None]
            sram_min = min(sram_mins) if sram_mins else None
            sram_final = info["iterations"][-1]["memory"].get("Internal SRAM", {}).get("free")

            psram_mins = [it["memory"]["SPI RAM (PSRAM)"]["minimum"] for it in info["iterations"]
                          if it["memory"].get("SPI RAM (PSRAM)", {}).get("minimum") is not None]
            psram_min = min(psram_mins) if psram_mins else None
            psram_final = info["iterations"][-1]["memory"].get("SPI RAM (PSRAM)", {}).get("free")

            lines.append(f"| Q{q_num:02} | {name} | {format_bytes_detail(sram_min)} | "
                         f"{format_bytes_detail(sram_final)} | {format_bytes_detail(psram_min)} | "
                         f"{format_bytes_detail(psram_final)} |")
    else:
        lines.extend([
            "| Query | Description | SRAM Final Free | PSRAM Final Free | "
            "PSRAM Largest Free Block |",
            "|---|---|---|---|---|",
        ])
        for q_num, info in sorted(queries_data.items()):
            name = QUERY_TITLES.get(q_num, f"Query {q_num}")
            sram_final = info["iterations"][-1]["memory"].get("Internal SRAM", {}).get("free")
            psram_final = info["iterations"][-1]["memory"].get("SPI RAM (PSRAM)", {}).get("free")
            psram_largest = info["iterations"][-1]["memory"].get(
                "SPI RAM (PSRAM)", {}).get("largest")
            lines.append(f"| Q{q_num:02} | {name} | {format_bytes_detail(sram_final)} | "
                         f"{format_bytes_detail(psram_final)} | "
                         f"{format_bytes_detail(psram_largest)} |")
    lines.append("")

    lines.extend([
        "## Detailed Iteration Results",
        "",
        "| Query | Phase | Iteration | Elapsed (ms) | SRAM Free | PSRAM Free |",
        "|---|---|---|---|---|---|",
    ])
    for q_num, info in sorted(queries_data.items()):
        for it in info["iterations"]:
            phase_str = it["phase"].capitalize()
            elapsed_str = f"{it['elapsed']:,}"
            sram_free = it["memory"].get("Internal SRAM", {}).get("free")
            psram_free = it["memory"].get("SPI RAM (PSRAM)", {}).get("free")
            lines.append(f"| Q{q_num:02} | {phase_str} | {it['iteration']} | {elapsed_str} | "
                         f"{format_bytes_detail(sram_free)} | {format_bytes_detail(psram_free)} |")
    lines.append("")

    sql_queries = {}
    for q_num in queries_data:
        if q_num in manifest_queries and manifest_queries[q_num].get("sql"):
            sql_queries[q_num] = manifest_queries[q_num]["sql"]
        else:
            try:
                sql_queries[q_num] = query_sql(q_num)
            except (OSError, FileNotFoundError):
                pass

    if sql_queries:
        lines.extend([
            "## Query Definitions",
            "",
        ])
        for q_num in sorted(sql_queries.keys()):
            name = QUERY_TITLES.get(q_num, f"Query {q_num}")
            sql = sql_queries[q_num]
            lines.extend([
                f"### Q{q_num:02}: {name}",
                "",
                "```sql",
                sql,
                "```",
                "",
            ])

    return "\n".join(lines) + "\n"


def resolve_report_paths(input_target=None, hardware_dir=None):
    if hardware_dir is None:
        hardware_dir = ROOT / "build/hardware"

    if input_target is None or not str(input_target).strip():
        if not hardware_dir.is_dir():
            raise RuntimeError(
                f"No hardware directory found at {hardware_dir}. Run 'make check-tpch' first.")
        csv_files = [p for p in hardware_dir.glob("tpch-*.csv")
                     if p.is_file() and p.stat().st_size > 0]
        if not csv_files:
            raise RuntimeError(
                f"No TPC-H benchmark results found in {hardware_dir}. Run 'make check-tpch' first.")
        csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        csv_path = csv_files[0]
        json_path = csv_path.with_suffix(".json")
        return csv_path, json_path if json_path.is_file() else None

    path = Path(input_target)
    candidates = [
        path,
        hardware_dir / path,
        hardware_dir / f"{path}.csv",
        hardware_dir / f"tpch-{path}.csv",
        ROOT / path,
    ]
    resolved = None
    for cand in candidates:
        if cand.is_file():
            resolved = cand
            break
        if cand.with_suffix(".csv").is_file():
            resolved = cand.with_suffix(".csv")
            break

    if resolved is None:
        raise RuntimeError(f"TPC-H benchmark file not found: {input_target}")

    if resolved.suffix == ".json":
        json_path = resolved
        csv_path = resolved.with_suffix(".csv")
    else:
        csv_path = resolved
        json_path = resolved.with_suffix(".json")

    if not csv_path.is_file():
        raise RuntimeError(f"TPC-H CSV file not found: {csv_path}")

    return csv_path, json_path if json_path.is_file() else None


def report(args):
    target = args.input or args.input_opt
    csv_path, json_path = resolve_report_paths(target)
    markdown = generate_markdown_report(csv_path, json_path)

    if args.output and str(args.output) == "-":
        sys.stdout.write(markdown)
        return

    output_path = args.output if args.output is not None else csv_path.with_suffix(".md")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    sys.stderr.write(f"Markdown report generated: {output_path}\n")

    if not args.quiet and (args.stdout or args.output is None):
        sys.stdout.write(markdown)


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
    hardware.add_argument("--threads", type=int, choices=(1, 2), default=1,
                          help="query threads (default: 1; 2 enables the CPU 1 worker)")
    hardware.add_argument("--queries", type=int, nargs="+", choices=QUERIES,
                          default=DEFAULT_QUERIES, help="query numbers (default: 1 6 12)")
    hardware.add_argument("--database",
                          help="use an existing /sdcard/NAME.duckdb without loading it")
    hardware.add_argument("--repeats", type=int, default=3, help="measured runs per query")
    hardware.add_argument("--timeout", type=float, default=120, help="seconds per command")
    report_cmd = commands.add_parser(
        "report", help="generate a Markdown report from TPC-H benchmark results")
    report_cmd.add_argument("input", nargs="?", type=Path, default=None,
                            help="CSV or JSON report file, or run ID (default: latest run)")
    report_cmd.add_argument("--input", "-i", dest="input_opt", type=Path,
                            help="alternative flag for input report file or run ID")
    report_cmd.add_argument("--output", "-o", type=Path,
                            help="path to write the Markdown report (default: <run>.md and stdout)")
    report_cmd.add_argument("--stdout", action="store_true",
                            help="print the Markdown report to stdout even if --output is specified")
    report_cmd.add_argument("--quiet", "-q", action="store_true",
                            help="suppress stdout output")
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
        elif args.action == "report":
            report(args)
        else:
            run(args)
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise SystemExit(detail) from error


if __name__ == "__main__":
    main()
