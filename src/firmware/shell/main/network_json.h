#pragma once

#include <cstdio>
#include <cstring>
#include <stdexcept>

class NetworkJson {
   public:
    NetworkJson(char* data, size_t capacity) : data(data), capacity(capacity) {}
    void append(const char* text) {
        size_t count = strlen(text);
        if (count >= capacity - used) throw std::length_error("Response too large");
        memcpy(data + used, text, count + 1);
        used += count;
    }
    void string(const char* text) {
        append("\"");
        for (const unsigned char* p = reinterpret_cast<const unsigned char*>(text); *p; ++p) {
            char escaped[7];
            if (*p < 32 || *p == '"' || *p == '\\') {
                snprintf(escaped, sizeof(escaped), "\\u%04x", *p);
            } else {
                escaped[0] = *p;
                escaped[1] = 0;
            }
            append(escaped);
        }
        append("\"");
    }

   private:
    char* data;
    size_t capacity;
    size_t used = 0;
};
