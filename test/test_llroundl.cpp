#include <cassert>
#include <cerrno>
#include <cfenv>
#include <cfloat>
#include <climits>
#include <cmath>
#include <limits>

int main() {
    struct Case {
        long double input;
        long long expected;
    };
    const Case cases[] = {{0.0L, 0},
                          {-0.0L, 0},
                          {0.49L, 0},
                          {-0.49L, 0},
                          {0.5L, 1},
                          {-0.5L, -1},
                          {1.5L, 2},
                          {-1.5L, -2},
                          {2.49L, 2},
                          {-2.49L, -2},
                          {static_cast<long double>(LLONG_MIN), LLONG_MIN},
                          {static_cast<long double>(LLONG_MIN) + 0.5L, LLONG_MIN},
                          {static_cast<long double>(LLONG_MAX) - 0.5L, LLONG_MAX},
                          {static_cast<long double>(LLONG_MAX), LLONG_MAX}};
    for (const auto& item : cases) {
        volatile long double input = item.input;
        errno = 0;
        std::feclearexcept(FE_ALL_EXCEPT);
        assert(llroundl(input) == item.expected);
        assert(errno == 0);
        const auto exceptions = std::fetestexcept(FE_INVALID | FE_INEXACT);
        assert(exceptions == 0);
    }
    const long double limit = -static_cast<long double>(LLONG_MIN);
    const long double invalid[] = {limit,
                                   -limit - 1.0L,
                                   limit - 0.5L,
                                   std::numeric_limits<long double>::infinity(),
                                   -std::numeric_limits<long double>::infinity(),
                                   std::numeric_limits<long double>::quiet_NaN()};
    for (volatile long double input : invalid) {
        errno = 0;
        std::feclearexcept(FE_ALL_EXCEPT);
        assert(llroundl(input) == LLONG_MIN);
        assert(errno == EDOM);
        const auto exceptions = std::fetestexcept(FE_INVALID);
        assert(exceptions != 0);
    }
    if (LDBL_MANT_DIG > 64) {
        volatile long double input = -limit - 0.5L;
        errno = 0;
        std::feclearexcept(FE_ALL_EXCEPT);
        assert(llroundl(input) == LLONG_MIN);
        assert(errno == EDOM);
        const auto exceptions = std::fetestexcept(FE_INVALID);
        assert(exceptions != 0);
    }
    std::feraiseexcept(FE_INEXACT);
    volatile long double input = 1.5L;
    assert(llroundl(input) == 2);
    const auto exceptions = std::fetestexcept(FE_INEXACT);
    assert(exceptions != 0);
}
