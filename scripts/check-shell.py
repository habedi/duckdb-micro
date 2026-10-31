"""Reset the P4 and check its command shell, SQL prompt, and SD CSV access."""

from pathlib import Path
import sys
import time
import uuid

import serial

if len(sys.argv) != 2:
    raise SystemExit("Usage: python scripts/check-shell.py /dev/ttyACM0")

log = Path(__file__).resolve().parents[1] / "build/hardware/shell-boot.log"
log.parent.mkdir(parents=True, exist_ok=True)
fatal = (b"Guru Meditation", b"abort() was called", b"assert failed:",
         b"task_wdt: Task watchdog got triggered", b"No bootable app")


def wait_for(marker, timeout=35):
    global pending
    deadline = time.monotonic() + timeout
    while True:
        if any(value in pending for value in fatal):
            raise RuntimeError("Firmware fault; see " + str(log))
        if marker in pending:
            _, pending = pending.split(marker, 1)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for " + repr(marker) + "; see " + str(log))
        block = port.read(4096)
        capture.write(block)
        capture.flush()
        pending += block


def command(line, marker, prompt=b"duckdb> "):
    port.write(line.encode() + b"\r")
    wait_for(marker)
    wait_for(prompt)


pending = b""
try:
    with serial.Serial(port=None, baudrate=115200, timeout=0.2, exclusive=True) as port, log.open("wb") as capture:
        port.port = sys.argv[1]
        port.dtr = False
        port.rts = False
        port.open()
        port.rts = True
        time.sleep(0.1)
        port.rts = False
        wait_for(b"MicroSD card mounted at /sdcard")
        wait_for(b"msh> ")
        command("clear", b"\x1b[H\x1b[2J", b"msh> ")
        time.sleep(0.1)  # Let linenoise distinguish this key from pasted input.
        port.write(b"\x0c")
        wait_for(b"\x1b[H\x1b[2J")
        wait_for(b"msh> ")
        port.write(b"duckdb --compact\r")
        wait_for(b"duckdb> ")
        time.sleep(0.1)  # Let linenoise distinguish this key from pasted input.
        port.write(b"\x0c")
        wait_for(b"\x1b[H\x1b[2J")
        wait_for(b"duckdb> ")
        command("SELECT 6 * 7 AS answer;", b"\n42\t")
        command("SELECT 6 * 7 AS answer", b"\n42\t")
        command("CREATE TABLE must_not_exist(i INT); SELECT 1;", b"Exactly one SQL statement")
        command("SELECT * FROM must_not_exist;", b"SQL error:")
        command("SELECT 'a' || chr(0) || 'b' || chr(10) || chr(27);", b"a\\0b\\n\\x1b\t")
        command("CREATE TABLE samples AS SELECT * FROM (VALUES (1), (2), (3)) t(x);", b"\n3\t")
        command("SELECT sum(x) FROM samples WHERE x > 1;", b"\n5\t")
        port.write(b"INSERT INTO samples SELECT * FROM range(105) RETURNING x;\r")
        wait_for(b"-- More -- [Space: next, q: quit]")
        port.write(b"q")
        wait_for(b"105 row(s)")
        wait_for(b"duckdb> ")
        command("SELECT count(*) FROM samples;", b"\n108\t")
        command("BEGIN TRANSACTION;", b"row(s)")
        command("INSERT INTO samples VALUES (9999);", b"\n1\t")
        port.write(b"SELECT * FROM range(105);\r")
        wait_for(b"-- More -- [Space: next, q: quit]")
        port.write(b"q")
        wait_for(b"(first 25 shown)")
        wait_for(b"duckdb> ")
        command("COMMIT;", b"row(s)")
        command("SELECT count(*) FROM samples;", b"\n109\t")
        command("SELECT length('hello');", b"\n5\t")
        command("SELECT INTERVAL 1 DAY = INTERVAL '24 hours';", b"\ntrue\t")
        command("SELECT list(i ORDER BY i) FROM range(3) t(i);", b"\n[0, 1, 2]\t")
        command("SELECT week(DATE '2021-01-01');", b"\n53\t")
        command("SELECT CAST(123456789012345678901234567890 AS HUGEINT) % 97;", b"\n52\t")
        command("SELECT * FROM missing_table;", b"SQL error:")
        command("SELECT 3 + 4;", b"\n7\t")
        pager = b"-- More -- [Space: next, q: quit]"
        port.write(b"SELECT * FROM range(105);\r")
        for last in (24, 49, 74, 99):
            wait_for(b"\n" + str(last).encode() + b"\t")
            wait_for(pager)
            port.write(b" ")
        wait_for(b"\n104\t")
        wait_for(b"duckdb> ")
        for key in (b"q", b"\x03"):
            port.write(b"SELECT * FROM range(105);\r")
            wait_for(pager)
            port.write(key)
            wait_for(b"(first 25 shown)")
            wait_for(b"duckdb> ")
            command("SELECT 3 + 4;", b"\n7\t")
        port.write(b".quit\r")
        wait_for(b"msh> ")
        print("PASS: interactive SQL, errors, pagination, and return to shell")
        command('duckdb -c "SELECT 100 + 23;"', b"\n123\t", b"msh> ")
        command('duckdb -c "SELECT * FROM range(105);"', b"(first 100 shown)", b"msh> ")
        command('duckdb -c "SELECT * FROM range(1000000000);"', b"100+ row(s)", b"msh> ")
        print("PASS: one-shot SQL and database reopen")

        fixture = "/sdcard/duckdb-probe-" + uuid.uuid4().hex + ".csv"
        command('write ' + fixture + ' "2,3"', b"Wrote to ", b"msh> ")
        port.write(b"duckdb --compact\r")
        wait_for(b"duckdb> ")
        try:
            sql = "SELECT a + b FROM read_csv('" + fixture + "', auto_detect=false, header=false, delim=',', columns={'a':'INTEGER','b':'INTEGER'}, buffer_size=65536);"
            command(sql, b"\n5\t")
            print("PASS: query CSV from SD card")
        finally:
            # This unique file belongs to this check only.
            port.write(b".quit\r")
            wait_for(b"msh> ")
            port.write(("rm " + fixture + "\r").encode())
            wait_for(b"\r\n")
            wait_for(b"msh> ")
        print("ALL SHELL CHECKS PASSED")
except (RuntimeError, serial.SerialException) as error:
    raise SystemExit(str(error))
