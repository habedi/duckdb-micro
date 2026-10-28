#include <cassert>

#include "utf8proc.hpp"

int main() {
    using namespace duckdb;
    utf8proc_int32_t output[4];
    int state = UTF8PROC_BOUNDCLASS_START;
    assert(utf8proc_decompose_char('a', output, 4, UTF8PROC_CHARBOUND, &state) == 2);
    assert(output[0] == -1 && output[1] == 'a');
    assert(utf8proc_decompose_char(0x301, output, 4, UTF8PROC_CHARBOUND, &state) == 1);
    assert(output[0] == 0x301);
    assert(utf8proc_decompose_char('b', output, 4, UTF8PROC_CHARBOUND, &state) == 2);
    assert(output[0] == -1 && output[1] == 'b');
    assert(utf8proc_decompose_char('a', output, 4, UTF8PROC_CHARBOUND, nullptr) == 2);
    assert(output[0] == -1 && output[1] == 'a');
}
