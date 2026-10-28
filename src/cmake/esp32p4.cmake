set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR riscv32)
set(CMAKE_C_COMPILER riscv32-esp-elf-gcc)
set(CMAKE_CXX_COMPILER riscv32-esp-elf-g++)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
# Only duckdb_static is built; upstream also declares an unused shared target.
set(CMAKE_POLICY_DEFAULT_CMP0164 OLD)
set(CMAKE_HAVE_LIBC_PTHREAD TRUE)
