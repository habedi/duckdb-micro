"""Check CPU 1 query execution, PSRAM stacks, and worker teardown on the board."""

import importlib.util
from pathlib import Path
import sys
import time
import uuid


spec = importlib.util.spec_from_file_location("stress", Path(__file__).with_name("check-stress.py"))
stress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stress)


def session(port, capture):
    output = bytearray()

    def command(line, expected=None, prompt=b"duckdb> "):
        output.extend(stress.command(port, capture, line, prompt, expected, timeout=120))

    command("duckdb --compact")
    command("SELECT current_setting('threads'), current_setting('async_threads');", "1\t0")
    command("SET threads=2;")
    sql = ("SELECT sum(i), sum(i*i) FROM (SELECT i FROM range(1000000) t(i) "
           "UNION ALL SELECT i FROM range(1000000) t(i)) t;")
    expected = "999999000000\t666665666667000000"
    command(sql, expected)
    command("SET threads=1;")
    command("SELECT current_setting('threads');", "1")
    command(sql, expected)
    command("SET threads=2;")
    command("SELECT current_setting('threads');", "2")
    command(sql, expected)
    command(".quit", prompt=b"msh> ")
    if output.count(b"Query worker: CPU 1, PSRAM stack yes") < 2:
        raise RuntimeError("Missing CPU 1 execution with a PSRAM stack before and after worker restart")


def main():
    import serial

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/check-dualcore.py /dev/ttyACM0")
    features = stress.features
    features.log = Path(__file__).resolve().parents[1] / f"build/hardware/dualcore-{uuid.uuid4().hex}.log"
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
        features.fatal += (b"ESP-ROM:", b"duckdb:", b"ALLOC_FAIL")
        streaks = {}
        baseline = None
        for cycle in range(5):
            session(port, capture)
            sample = stress.memory_sample(stress.command(port, capture, "free"))
            if baseline is None:
                baseline = sample
            else:
                stress.check_memory(baseline, sample, streaks, 5)
            print(f"Session {cycle + 1}: CPU 1, PSRAM stack, correct results, worker restart; "
                  f"internal free={sample['Internal SRAM']['free']}", flush=True)
    print("PASS: dual-core query execution and worker teardown")


if __name__ == "__main__":
    main()
