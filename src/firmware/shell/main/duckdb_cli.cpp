#include <cctype>

#include "duckdb/parser/parser.hpp"
#include "linenoise/linenoise.h"
#include "shell_prompt.hpp"
#include "shell_state.hpp"
#include "terminal.hpp"

extern "C" bool shell_resolve_arg(const char* cmd, const char* in, char* out, size_t out_len);

extern "C" size_t shell_command_line_limit(void);

namespace {
void cli_complete(const char* input, linenoiseCompletions* completions) {
    try {
        const std::string line(input);
        const auto limit = shell_command_line_limit();
        if (line.size() + 1 >= limit) return;
        if (!line.empty() && line[0] == '.') {
            for (const auto& candidate :
                 duckdb_shell::ShellState::GetMetadataCompletions(input, line.size())) {
                if (completions->len == 16) break;
                linenoiseAddCompletion(completions, candidate.c_str());
            }
            return;
        }
        const auto end = line.size();
        auto start = end;
        while (start && (std::isalnum(static_cast<unsigned char>(line[start - 1])) ||
                         line[start - 1] == '_'))
            --start;
        if (start == end) return;
        const auto prefix = duckdb::StringUtil::Lower(line.substr(start));
        for (const auto& keyword : duckdb::Parser::KeywordList()) {
            if (!duckdb::StringUtil::StartsWith(keyword.name, prefix)) continue;
            if (completions->len == 16) break;
            const auto candidate = line.substr(0, start) + duckdb::StringUtil::Upper(keyword.name);
            if (candidate.size() + 1 < limit)
                linenoiseAddCompletion(completions, candidate.c_str());
        }
    } catch (const std::exception&) {
        // Completion is optional when the query engine has little memory left.
    }
}
}  // namespace

bool esp_cli_read_file(duckdb_shell::ShellState& state, const duckdb::string& path) {
    char resolved[512];
    if (!shell_resolve_arg(".read", path.c_str(), resolved, sizeof(resolved))) return false;
    if (state.safe_mode || state.active_read_files.size() >= 4 ||
        state.active_read_files.count(resolved)) {
        state.PrintDatabaseError(".read is disabled, recursive, or exceeds four nested files");
        return false;
    }
    std::unique_ptr<FILE, decltype(&fclose)> file(fopen(resolved, "rb"), fclose);
    if (!file) {
        state.PrintDatabaseError("Cannot open SQL file");
        return false;
    }
    struct Restore {
        duckdb_shell::ShellState& state;
        FILE* saved;
        int line;
        const char* path;
        ~Restore() {
            state.in = saved;
            state.lineno = line;
            state.active_read_files.erase(path);
        }
    } restore{state, state.in, state.lineno, resolved};
    state.active_read_files.insert(resolved);
    state.in = file.get();
    return state.ProcessInput(duckdb_shell::InputMode::FILE) == 0;
}

namespace duckdb {
TerminalSize Terminal::GetTerminalSize() { return {80, 24}; }
bool Terminal::IsAtty() { return true; }
}  // namespace duckdb

int duckdb_cli_run(const char* path) {
    using namespace duckdb_shell;
    struct Reset {
        ~Reset() { ShellState::GetReference() = nullptr; }
    } reset;
    auto state = duckdb::make_uniq<ShellState>();
    ShellState::GetReference() = state.get();
    state->out = stdout;
    state->Initialize();
    state->main_prompt->ParsePrompt("duckdb> ");
    snprintf(state->continuePrompt, sizeof(state->continuePrompt), "   ...> ");
    state->config.SetOptionByName("threads", duckdb::Value("1"));
    state->config.SetOptionByName("async_threads", duckdb::Value("0"));
    state->config.SetOptionByName("memory_limit", duckdb::Value("8MB"));
    state->config.SetOptionByName("temp_directory", duckdb::Value(""));
    state->zDbFilename = path ? path : ":memory:";
    state->highlighting_enabled = false;
    state->pager_mode = PagerMode::PAGER_OFF;
    state->max_width = 80;
    state->max_rows = 40;
    state->max_analyze_rows = 40;
    state->OpenDB();
    linenoiseSetCompletionCallback(cli_complete);
    linenoiseSetHintsCallback(nullptr);
    printf("DuckDB upstream CLI. End SQL with ;. .help lists commands; .quit returns to msh.\n");
    return state->ProcessInput(InputMode::STANDARD);
}
