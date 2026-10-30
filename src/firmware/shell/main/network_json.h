#pragma once

#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string_view>

class NetworkJson {
   public:
    NetworkJson(char* data, size_t capacity) : data(data), capacity(capacity) {}
    void append(const char* text) {
        size_t count = strlen(text);
        if (count >= capacity - used) throw std::length_error("Response too large");
        memcpy(data + used, text, count + 1);
        used += count;
    }
    void string(std::string_view text) {
        append("\"");
        for (unsigned char c : text) {
            char escaped[7];
            if (c < 32 || c == '"' || c == '\\') {
                snprintf(escaped, sizeof(escaped), "\\u%04x", c);
            } else {
                escaped[0] = c;
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
