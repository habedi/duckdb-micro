"""Verify CSV downloads and authenticated read-only REST queries over board Wi-Fi."""

import argparse
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import re
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import uuid

spec = importlib.util.spec_from_file_location("tpch", Path(__file__).with_name("check-tpch.py"))
tpch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tpch)
stress = tpch.stress
CSV = b"id,name\n1,one\n2,two\n"
GRAPH_URL = ("https://huggingface.co/datasets/habedi/graphina-graphs/raw/"
             "668d9c9081be724b5f6a66fa1c8442c2bdf87f90/wikipedia_chameleon.txt")
GRAPH_AGGREGATE = "count(*), sum(id1), sum(id2)"
GRAPH_EXPECTED = "36101\t40807754\t52997576"


def download_graph(command, fixture):
    output = command(f"fetch_csv {GRAPH_URL} {fixture}.graph.csv", timeout=60)
    if b"Downloaded 332440 bytes" not in output:
        raise RuntimeError(f"Hugging Face HTTPS download failed: {output!r}")
    command(f'duckdb -c "SELECT {GRAPH_AGGREGATE} FROM '
            f'read_csv(\'{fixture}.graph.csv\', buffer_size=65536, max_line_size=1024);"',
            expected=GRAPH_EXPECTED)


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/csv")
            self.end_headers()
            return
        if self.path not in ("/csv", "/chunked", "/truncated"):
            self.send_error(404)
            return
        self.send_response(200)
        if self.path == "/chunked":
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Content-Length", str(len(CSV) + (10 if self.path == "/truncated" else 0)))
        self.end_headers()
        self.wfile.write(f"{len(CSV):x}\r\n".encode() + CSV + b"\r\n0\r\n\r\n"
                         if self.path == "/chunked" else CSV)
        self.close_connection = True

    def log_message(self, *args):
        pass


def rest(address, token, sql, expected=200):
    headers = {"Content-Type": "text/plain"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(f"http://{address}:8080/query", data=sql.encode(), headers=headers)
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=60)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        if response.code != expected:
            raise RuntimeError(f"REST status {response.code}, expected {expected}: {body!r}")
    return json.loads(body) if expected == 200 else body


def run(port, capture, fixture):
    def command(line, **kwargs):
        return tpch.send_command(port, capture, line, **kwargs)

    address = None
    for _ in range(20):
        output = command("ifconfig")
        match = re.search(rb"inet (\d+\.\d+\.\d+\.\d+)", output)
        if match and match[1] != b"0.0.0.0":
            address = match[1].decode()
            break
        time.sleep(1)
    if address is None:
        raise RuntimeError("Connect board Wi-Fi with wifi_connect before running this check")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect((address, 8080))
        host = route.getsockname()[0]
    with ThreadingHTTPServer(("0.0.0.0", 0), FixtureHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://{host}:{server.server_port}"
            for suffix, endpoint in (("csv", "/csv"), ("chunk.csv", "/chunked")):
                output = command(f"fetch_csv {base}{endpoint} {fixture}.{suffix}")
                if b"Downloaded " not in output:
                    raise RuntimeError(f"Download failed: {output!r}")
                command(f'duckdb -c "SELECT count(*) FROM read_csv(\'{fixture}.{suffix}\');"',
                        expected="2")
            for endpoint in ("/truncated", "/redirect", "/missing"):
                output = command(f"fetch_csv {base}{endpoint} {fixture}.failed")
                if b"download failed" not in output:
                    raise RuntimeError(f"Expected download rejection: {output!r}")
                output = command(f"cat {fixture}.failed")
                if b"No such file" not in output:
                    raise RuntimeError("Failed download left a file")
            output = command(f"fetch_csv {base}/csv {fixture}.csv")
            if b"download failed" not in output:
                raise RuntimeError("Download overwrote an existing file")
        finally:
            server.shutdown()
            thread.join()
    download_graph(command, fixture)
    output = command(f"fetch_csv https://expired.badssl.com/ {fixture}.expired", timeout=30)
    if b"download failed" not in output or b"Downloaded " in output:
        raise RuntimeError("Expired HTTPS certificate was accepted")
    output = command(f"cat {fixture}.expired")
    if b"No such file" not in output:
        raise RuntimeError("Rejected HTTPS download left a file")
    command(f'duckdb {fixture}.duckdb -c "CREATE TABLE samples AS '
            f'SELECT * FROM read_csv(\'{fixture}.csv\');"', expected="2")
    command(f'duckdb {fixture}.duckdb -c "CREATE TABLE edges AS SELECT * FROM '
            f'read_csv(\'{fixture}.graph.csv\', buffer_size=65536, max_line_size=1024);"',
            expected="36101")
    previous = None
    baseline = None
    streaks = {}
    for _ in range(5):
        output = command(f"query_server start {fixture}.duckdb")
        match = re.search(rb"Authorization: Bearer ([a-f0-9]{48})", output)
        if not match:
            raise RuntimeError(f"Missing server token: {output!r}")
        token = match[1].decode()
        if token == previous:
            raise RuntimeError("Server token was reused")
        previous = token
        rest(address, None, "SELECT 42", 401)
        rest(address, "wrong", "SELECT 42", 401)
        result = rest(address, token, "SELECT sum(id) FROM samples")
        if result["rows"] != [["3"]] or result["truncated"]:
            raise RuntimeError(f"Incorrect REST result: {result}")
        result = rest(address, token, f"SELECT {GRAPH_AGGREGATE} FROM edges")
        if result["rows"] != [GRAPH_EXPECTED.split("\t")] or result["truncated"]:
            raise RuntimeError(f"Incorrect Hugging Face graph result: {result}")
        result = rest(address, token, "SELECT NULL AS missing, 'a' || chr(10) || chr(34) AS escaped")
        if result["rows"] != [[None, 'a\n"']]:
            raise RuntimeError(f"Incorrect JSON escaping: {result}")
        result = rest(address, token, "SELECT * FROM range(101)")
        if len(result["rows"]) != 100 or not result["truncated"]:
            raise RuntimeError("Missing row truncation")
        for sql in ("", "x" * 4097, "SELECT 1\x00", "SELECT 1; SELECT 2;",
                    "DELETE FROM samples", f"SELECT * FROM read_csv('{fixture}.csv')",
                    "SELECT repeat('x', 70000)"):
            rest(address, token, sql, 400)
        command("duckdb", prompt=b"duckdb> ")
        rest(address, token, "SELECT 1", 503)
        command(".quit")
        command("query_server stop")
        sample = stress.memory_sample(command("free"))
        if baseline is None:
            baseline = sample
        else:
            stress.check_memory(baseline, sample, streaks, 5)
    for suffix in ("csv", "chunk.csv", "graph.csv", "duckdb"):
        command(f"rm {fixture}.{suffix}")


def main():
    import serial

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tty")
    args = parser.parse_args()
    run_id = uuid.uuid4().hex
    log = Path(__file__).resolve().parents[1] / f"build/hardware/network-{run_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    stress.features.log = log
    fixture = f"/sdcard/network-{run_id}"
    print(f"Capture: {log}\nFixture: {fixture}", flush=True)
    with serial.Serial(port=None, baudrate=115200, timeout=0.2, exclusive=True) as port, log.open("wb") as capture:
        port.port = args.tty
        port.dtr = port.rts = False
        port.open()
        port.rts = True
        time.sleep(0.1)
        port.rts = False
        stress.features.pending = b""
        stress.features.wait_for(port, capture, b"msh> ", timeout=35)
        stress.features.fatal += (b"ESP-ROM:", b"ALLOC_FAIL")
        try:
            run(port, capture, fixture)
        finally:
            with contextlib.suppress(Exception):
                stress.command(port, capture, "query_server stop")
    print("PASS: HTTP/HTTPS downloads, REST access controls, results, and server restarts")


if __name__ == "__main__":
    main()
