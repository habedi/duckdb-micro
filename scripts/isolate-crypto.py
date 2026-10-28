"""Keep DuckDB's bundled mbedTLS symbols separate from ESP-IDF's version."""

from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
archive = root / "build/duckdb/src/libduckdb_static.a"
output = archive.with_name("libduckdb_esp32p4.a")


def crypto_symbols(path):
    listing = subprocess.check_output(
        ["riscv32-esp-elf-nm", "-g", "--format=posix", str(path)], text=True
    )
    return {
        line.split()[0]
        for line in listing.splitlines()
        if line.startswith(("mbedtls_", "psa_"))
    }


symbols = crypto_symbols(archive)
assert symbols, "Bundled crypto symbols were not found"
mapping = archive.with_name("crypto-symbols.txt")
mapping.write_text("".join(f"{name} duckdb_port_{name}\n" for name in sorted(symbols)))
subprocess.run(
    ["riscv32-esp-elf-objcopy", f"--redefine-syms={mapping}", str(archive), str(output)],
    check=True,
)
assert not crypto_symbols(output), "Bundled crypto symbols still conflict with ESP-IDF"
print(f"Isolated {len(symbols)} bundled crypto symbols")
