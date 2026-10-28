#include <cerrno>
#include <cfenv>
#include <cfloat>
#include <climits>

static_assert(LDBL_MANT_DIG >= 64, "This shim requires a wide long double");

static long long invalid_round() noexcept {
    errno = EDOM;
    std::feraiseexcept(FE_INVALID);
    return LLONG_MIN;
}

static long long round_value(long double value) noexcept {
    constexpr long double limit = -static_cast<long double>(LLONG_MIN);
    if (!(value > -limit - 1.0L && value < limit)) {
        return invalid_round();
    }
    const auto integer = static_cast<long long>(value);
    const auto fraction = value - static_cast<long double>(integer);
    if (fraction >= 0.5L) {
        return integer == LLONG_MAX ? invalid_round() : integer + 1;
    }
    if (fraction <= -0.5L) {
        return integer == LLONG_MIN ? invalid_round() : integer - 1;
    }
    return integer;
}

// Espressif GCC 15.2's RISC-V picolibc llroundl recursively calls itself.
extern "C" long long llroundl(long double value) noexcept {
    std::fexcept_t inexact{};
    std::fegetexceptflag(&inexact, FE_INEXACT);
    const auto result = round_value(value);
    std::fesetexceptflag(&inexact, FE_INEXACT);
    return result;
}
