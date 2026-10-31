"""Verify LittleFS, DuckDB JSON extension, and DuckDB Parquet extension on hardware."""

from pathlib import Path
import sys
import time
import uuid

log = Path(__file__).resolve().parents[1] / "build/hardware/features-check.log"
fatal = (b"Guru Meditation", b"abort() was called", b"assert failed:",
         b"task_wdt: Task watchdog got triggered", b"No bootable app", b"SQL error:")

pending = b""


def wait_for(port, capture, marker, timeout=25):
    global pending
    deadline = time.monotonic() + timeout
    while True:
        if any(value in pending for value in fatal):
            raise RuntimeError("Firmware fault detected; see " + str(log))
        if marker in pending:
            idx = pending.find(marker)
            matched = pending[:idx + len(marker)]
            pending = pending[idx + len(marker):]
            return matched
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for {marker!r}; pending={pending!r}; see {log}")
        block = port.read(4096)
        if block:
            capture.write(block)
            capture.flush()
            pending += block


def exec_msh(port, capture, cmd, expected_marker):
    port.write(cmd.encode() + b"\r")
    wait_for(port, capture, expected_marker)
    wait_for(port, capture, b"msh> ")


def exec_duckdb(port, capture, sql, expected_marker):
    port.write(sql.encode() + b"\r")
    wait_for(port, capture, expected_marker)
    wait_for(port, capture, b"duckdb> ")


def main():
    import serial

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/check-features.py /dev/ttyACM0")
    log.parent.mkdir(parents=True, exist_ok=True)
    fixture = "/data/duckdb-features-" + uuid.uuid4().hex
    greeting = fixture + ".txt"
    json_file = fixture + ".json"
    parquet_file = fixture + ".parquet"

    try:
        with serial.Serial(port=None, baudrate=115200, timeout=0.2, exclusive=True) as port, log.open("wb") as capture:
            port.port = sys.argv[1]
            port.dtr = False
            port.rts = False
            port.open()
            port.rts = True
            time.sleep(0.1)
            port.rts = False

            print("1. Waiting for shell boot...")
            wait_for(port, capture, b"msh> ")
            print("   Boot OK")

            print("2. Checking filesystem df...")
            exec_msh(port, capture, "df", b"/data")
            print("   PASS: LittleFS (/data) present in df")

            print("3. Testing LittleFS write and read...")
            exec_msh(port, capture, f"write {greeting} hello_littlefs", f"Wrote to {greeting}".encode())
            exec_msh(port, capture, f"cat {greeting}", b"\nhello_littlefs\r\n")
            exec_msh(port, capture, "ls /data", Path(greeting).name.encode())
            exec_msh(port, capture, f"rm {greeting}", b"\r\n")
            print("   PASS: LittleFS write, read, ls, and rm verified")

            print("4. Testing DuckDB JSON extension...")
            port.write(b"duckdb --compact\r")
            wait_for(port, capture, b"duckdb> ")

            # JSON scalar functions
            exec_duckdb(port, capture, "SELECT json_extract('{\"score\": 99}', '$.score');", b"\n99\t")
            exec_duckdb(port, capture, "SELECT json_array_length('[10, 20, 30]');", b"\n3\t")
            print("   PASS: JSON scalar functions")

            # JSON file querying via read_json
            port.write(b".quit\r")
            wait_for(port, capture, b"msh> ")
            exec_msh(port, capture, 'write ' + json_file + ' {"name":"p4","cores":2}', f"Wrote to {json_file}".encode())

            port.write(b"duckdb --compact\r")
            wait_for(port, capture, b"duckdb> ")
            exec_duckdb(port, capture, f"SELECT name, cores FROM read_json('{json_file}');", b"\np4\t2\t")
            print("   PASS: read_json on /data")

            print("5. Testing DuckDB Parquet extension...")
            # Parquet write (COPY TO)
            exec_duckdb(port, capture, f"COPY (SELECT 123 AS id, 'duckdb-p4' AS tag) TO '{parquet_file}' (FORMAT PARQUET);", b"\n1\t")
            print("   PASS: Parquet write (COPY TO)")

            # Parquet read (read_parquet)
            exec_duckdb(port, capture, f"SELECT id, tag FROM read_parquet('{parquet_file}');", b"\n123\tduckdb-p4\t")
            print("   PASS: Parquet read (read_parquet)")

            # Cleanup
            port.write(b".quit\r")
            wait_for(port, capture, b"msh> ")
            exec_msh(port, capture, f"rm {json_file}", b"\r\n")
            exec_msh(port, capture, f"rm {parquet_file}", b"\r\n")

            print("\nALL LITTLEFS, JSON, AND PARQUET CHECKS PASSED ON HARDWARE!")
    except (RuntimeError, serial.SerialException) as error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
