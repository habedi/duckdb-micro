#include "linenoise/linenoise.h"
#include "shell_state.hpp"

extern "C" bool esp_cli_sql_complete(const char* input, linenoiseCompletions* completions,
                                     size_t limit) {
    auto& state = duckdb_shell::ShellState::Get();
    struct Restore {
        duckdb_shell::ShellState& state;
        duckdb::unique_ptr<duckdb::QueryResult> result;
        bool referenced;
        ~Restore() {
            state.last_result = std::move(result);
            state.last_result_referenced = referenced;
        }
    } restore{state, std::move(state.last_result), state.last_result_referenced};
    try {
        const duckdb::string line(input);
        auto result = state.conn->Query(
            "SELECT suggestion, suggestion_start FROM sql_auto_complete($1, "
            "max_suggestion_count=16, max_file_suggestion_count=16, "
            "max_exact_suggestion_count=16) LIMIT 16",
            duckdb::Value(line));
        if (result->HasError()) return false;
        for (auto& row : *result) {
            auto start = row.GetValue<int64_t>(1);
            if (start < 0 || static_cast<size_t>(start) > line.size()) continue;
            auto candidate =
                line.substr(0, static_cast<size_t>(start)) + row.GetValue<duckdb::string>(0);
            if (candidate.size() + 1 >= limit) continue;
            if (completions->len == 16) break;
            linenoiseAddCompletion(completions, candidate.c_str());
        }
        return completions->len != 0;
    } catch (const std::exception&) {
        return false;
    }
}
