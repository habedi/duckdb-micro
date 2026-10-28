"""Reset the flashed P4 and require the firmware's SQL self-check to pass."""

from pathlib import Path
import re
import sys
import time

import serial

if len(sys.argv) != 2:
    raise SystemExit("Usage: python scripts/check-board.py /dev/ttyACM0")

log = Path(__file__).resolve().parents[1] / "build/hardware/probe-boot.log"
log.parent.mkdir(parents=True, exist_ok=True)
output = b""
failures = (
    b"probe: FAIL:", b"Guru Meditation", b"No bootable app",
    b"abort() was called", b"assert failed:",
    b"task_wdt: Task watchdog got triggered",
)
with serial.Serial(port=None, baudrate=115200, timeout=0.2, exclusive=True) as port:
    port.port = sys.argv[1]
    port.dtr = False
    port.rts = False
    port.open()
    port.rts = True
    time.sleep(0.1)
    port.rts = False
    deadline = time.monotonic() + 120
    with log.open("wb") as capture:
        while time.monotonic() < deadline:
            block = port.read(4096)
            capture.write(block)
            capture.flush()
            output += block
            if b"ALL SQL CHECKS PASSED" in output or any(marker in output for marker in failures):
                break

text = output.decode(errors="replace")
text = re.sub(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", "<MAC withheld>", text)
print(text)
if b"ALL SQL CHECKS PASSED" not in output or any(marker in output for marker in failures):
    raise SystemExit("Board SQL check failed or timed out; see build/hardware/probe-boot.log")
