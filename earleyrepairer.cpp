#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <unordered_set>
#include <iterator>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <cstring>
#include <cerrno>
#include <csignal>

extern char **environ;

/*────────────────── Statistics ──────────────────*/
static long long ORACLE = 0, OK = 0, BAD = 0, INC = 0;
static long long MAX_ORACLE = (long long)1e18;
enum class Res { OK, ERR, INC };

/*────────────────── Character set ───────────────*/
// Was: std::set<char>. Now use flat vector to avoid tree overhead.
class CharSet {
    std::vector<char> chars_;
public:
    CharSet() {
        chars_.reserve(128);
        for (int c = 33; c <= 126; ++c) chars_.push_back(static_cast<char>(c));
        chars_.push_back('\n'); chars_.push_back('\t');
    }
    auto begin() const { return chars_.begin(); }
    auto end()   const { return chars_.end();   }
};

/*────────────────── Grammar basics ───────────────*/
const std::string Any   = "$.";
const std::string Empty = "<$>";

using RuleMap = std::map<std::string,
                         std::vector<std::vector<std::string>>>;

struct Grammar {
    RuleMap R;

    void add(const std::string& lhs, std::vector<std::string> rhs) {
        R[lhs].push_back(std::move(rhs));
    }

    Grammar covering() const {
        Grammar cg;
        for (const auto& [lhs, rhss] : R) {
            for (const auto& rhs : rhss) {
                if (rhs.size() == 1 && !R.count(rhs[0])) {
                    const std::string& t = rhs[0];
                    if (t == "\0") {
                        cg.add(lhs, {}); // <cN> → ε
                    } else {
                        const std::string delTok = "<$del[" + t + "]>";
                        const std::string negTok = "<$!["  + t + "]>";
                        // Order: match | delete | insert-before | substitute
                        cg.add(lhs, {t});
                        cg.add(lhs, {delTok});
                        cg.add(lhs, {Any, t});
                        cg.add(lhs, {negTok});
                    }
                } else {
                    cg.add(lhs, rhs);
                }
            }
        }
        return cg;
    }

    static Grammar fromString(const std::string& str,
                              const std::string& start = "<start>")
    {
        Grammar g;
        std::vector<std::string> start_rhs;
        std::size_t idx = 0;

        start_rhs.reserve(str.size() + 1);
        for (char c : str) {
            std::string nt = "<c" + std::to_string(idx++) + ">";
            start_rhs.push_back(nt);
            g.add(nt, {std::string(1, c)});
        }
        // sentinel \0
        std::string nt_end = "<c" + std::to_string(idx) + ">";
        g.add(nt_end, {"\0"});
        start_rhs.push_back(nt_end);

        g.add(start, std::move(start_rhs));
        return g;
    }
};

struct Prod { std::string lhs; std::vector<std::string> rhs; };

struct EditApp {
    const Prod* p = nullptr;
    bool applied = false;
    bool char_used = false;
    char ch = 0;
    bool needChar = false;
};

/*──────── String generation ────────*/
static std::string gen_multi(const std::string& sym,
                             const RuleMap& cov,
                             std::vector<EditApp>& apps,
                             int active)
{
    if (sym == Empty) return "";

    if (sym == Any) {
        if (active >= 0) {
            auto& a = apps[active];
            if (a.ch && !a.char_used) { a.char_used = true; return std::string(1, a.ch); }
        }
        return "";
    }

    if (sym.rfind("<$![", 0) == 0) {
        if (active >= 0) {
            auto& a = apps[active];
            if (a.ch && !a.char_used) { a.char_used = true; return std::string(1, a.ch); }
        }
        return "";
    }

    if (sym.rfind("<$del[", 0) == 0) {
        return "";
    }

    auto it = cov.find(sym);
    if (it == cov.end()) {
        return sym == "\0" ? "" : sym;
    }

    if (active == -1) {
        for (size_t i = 0; i < apps.size(); ++i) {
            auto& a = apps[i];
            if (!a.applied && sym == a.p->lhs) {
                a.applied = true;
                std::string out;
                out.reserve(16);
                for (const auto& s : a.p->rhs)
                    out += gen_multi(s, cov, apps, int(i));
                return out;
            }
        }
    }

    const auto& first_rhs = it->second.front();
    std::string out;
    out.reserve(16);
    for (const auto& s : first_rhs)
        out += gen_multi(s, cov, apps, active);
    return out;
}

/*────────────────── oracle wrapper ───────────────*/
// New approach: write candidate to a temporary file and pass the file path to the parser.
// Avoids fd-pipe issues when invoked from Python subprocess on macOS.
namespace {
    static bool write_all(int fd, const char* buf, size_t len) {
        size_t off = 0;
        while (off < len) {
            ssize_t n = ::write(fd, buf + off, len - off);
            if (n < 0) {
                if (errno == EINTR) continue;
                std::cerr << "[ERROR] write_all: " << strerror(errno) << " (errno=" << errno << ")\n";
                return false;
            }
            off += static_cast<size_t>(n);
        }
        return true;
    }
}

static std::function<Res(const std::string&)> oracleWrap(const std::string& exe)
{
    constexpr int TIMEOUT_MS = 250;

    return [exe](const std::string& in) -> Res {
        if (ORACLE >= MAX_ORACLE) return Res::ERR;
        ++ORACLE;

        // 1) Create anonymous pipe
        int pfd[2];
        if (pipe(pfd) == -1) {
            std::cerr << "[ERROR] pipe failed: " << strerror(errno) << "\n";
            ++BAD; return Res::ERR;
        }

        // 2) Prepare child process (parser) spawn
        posix_spawn_file_actions_t fa;
        posix_spawn_file_actions_init(&fa);
        // Redirect stdin (fd 0) to read end of pipe
        posix_spawn_file_actions_adddup2(&fa, pfd[0], STDIN_FILENO);
        posix_spawn_file_actions_addclose(&fa, pfd[1]);
        posix_spawn_file_actions_addclose(&fa, pfd[0]);
        // Redirect stdout/stderr to /dev/null
        posix_spawn_file_actions_addopen(&fa, STDOUT_FILENO, "/dev/null", O_WRONLY, 0);
        posix_spawn_file_actions_addopen(&fa, STDERR_FILENO, "/dev/null", O_WRONLY, 0);

        // If the parser expects a filename, use "-" for stdin, else remove argument
        char* argvv[] = { const_cast<char*>(exe.c_str()), (char*)"-", nullptr };

        pid_t pid;
        int rc = posix_spawn(&pid, exe.c_str(), &fa, nullptr, argvv, environ);
        posix_spawn_file_actions_destroy(&fa);

        if (rc != 0) {
            std::cerr << "[ERROR] posix_spawn failed: " << strerror(rc) << "\n";
            close(pfd[0]);
            close(pfd[1]);
            ++BAD; return Res::ERR;
        }

        // Parent: close read end, write candidate to write end
        close(pfd[0]);
        bool w = write_all(pfd[1], in.data(), in.size());
        close(pfd[1]);
        if (!w) {
            ++BAD; return Res::ERR;
        }

        // 3) Wait with timeout
        int st = 0;
        auto start = std::chrono::steady_clock::now();
        int sleep_us = 500;
        while (true) {
            pid_t r = ::waitpid(pid, &st, WNOHANG);
            if (r == -1) { std::cerr << "[ERROR] waitpid error\n"; ++BAD; return Res::ERR; }
            if (r > 0) break;

            auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                               std::chrono::steady_clock::now() - start).count();
            if (elapsed >= TIMEOUT_MS) {
                ::kill(pid, SIGKILL);
                ::waitpid(pid, &st, 0);
                std::cerr << "[ERROR] parser timeout\n";
                ++BAD; return Res::ERR;
            }
            ::usleep(sleep_us);
            if (sleep_us < 2000) sleep_us = std::min(2000, sleep_us * 2);
        }

        if (WIFEXITED(st)) {
            switch (WEXITSTATUS(st)) {
                case 0:   ++OK;  return Res::OK;
                case 1:   ++BAD; return Res::ERR;
                case 255: ++INC; return Res::INC;
                default:  ++BAD; return Res::ERR;
            }
        } else {
            ++BAD; return Res::ERR;
        }
    };
}

/*────────────────── main ─────────────────────────*/
int main(int argc, char* argv[])
{
    // Ignore SIGPIPE to be safe
    signal(SIGPIPE, SIG_IGN);

    const int MAX_EDITS = 5;

    try {
        if (argc < 4) {
            std::cerr << "Usage: " << argv[0]
                      << " <parser_path> <input_string_or_file> <output_file>\n";
            return 1;
        }
        const std::string exe      = argv[1];
        const std::string inputArg = argv[2];
        const std::string outF     = argv[3];

        if (access(exe.c_str(), X_OK) != 0) {
            std::cerr << "Parser executable not found or not executable: " << exe << "\n";
            return 1;
        }

        // argv[2] can be literal or a path to a file.
        std::string input;
        {
            std::ifstream fin(inputArg, std::ios::binary);
            if (fin.good()) {
                fin.seekg(0, std::ios::end);
                std::streamsize sz = fin.tellg();
                if (sz < 0) sz = 0;
                input.resize(static_cast<size_t>(sz));
                fin.seekg(0, std::ios::beg);
                fin.read(&input[0], sz);
            } else {
                input = inputArg;
            }
        }

        auto oracle  = oracleWrap(exe);

        Grammar base = Grammar::fromString(input);
        Grammar cov  = base.covering();

        /* 0-edit quick check */
        if (oracle(input) == Res::OK) {
            std::ofstream(outF, std::ios::binary) << input;
            std::cout << "Repaired string: " << input << "\n";
            printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld incomplete: %lld ***\n",
                   ORACLE, OK, BAD, INC);
            return 0;
        }

        /* collect all single-edit productions (insert/delete/substitute) */
        std::vector<Prod> edits;
        edits.reserve(cov.R.size() * 3);
        for (const auto& [lhs, rhss] : cov.R) {
            for (const auto& rhs : rhss) {
                bool is_insert = (!rhs.empty() && rhs[0] == Any);
                bool is_delete = (rhs.size()==1 && rhs[0].rfind("<$del[", 0) == 0);
                bool is_subst  = (rhs.size()==1 && rhs[0].rfind("<$![",  0) == 0);
                if (is_insert || is_delete || is_subst) edits.push_back({lhs, rhs});
            }
        }

        CharSet cs;

        // Cached oracle to avoid duplicate work
        std::unordered_set<std::string> seen;
        seen.reserve(1u << 20);
        auto oracle_cached = [&](const std::string& s) -> Res {
            if (seen.insert(s).second) return oracle(s);
            return Res::ERR;
        };

        auto needsChar = [&](const Prod& p) -> bool {
            return (!p.rhs.empty() && p.rhs[0] == Any) ||
                   (p.rhs.size()==1 && p.rhs[0].rfind("<$![", 0) == 0);
        };

        const size_t input_len = input.size();

        // Build and test a candidate given selected edits + chars
        std::function<bool(const std::vector<int>&, const std::vector<char>&)> build_and_test =
        [&](const std::vector<int>& sel, const std::vector<char>& chars) -> bool
        {
            std::vector<EditApp> apps; apps.reserve(sel.size());
            size_t ci = 0;
            for (int idx : sel) {
                EditApp a;
                a.p = &edits[idx];
                a.needChar = needsChar(*a.p);
                if (a.needChar) a.ch = chars[ci++];
                apps.push_back(a);
            }

            std::string cand;
            cand.reserve(input_len + sel.size() * 2);

            cand += gen_multi("<start>", cov.R, apps, -1);
            for (const auto& a : apps) if (!a.applied) return false;

            if (oracle_cached(cand) == Res::OK) {
                std::ofstream(outF, std::ios::binary) << cand;
                std::cout << "Repaired string: " << cand << "\n";
                printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld incomplete: %lld ***\n",
                       ORACLE, OK, BAD, INC);
                return true;
            }
            return false;
        };

        // Assign characters for edits that need one (bounded)
        std::function<bool(const std::vector<int>&, size_t, std::vector<char>&)> assign_chars =
        [&](const std::vector<int>& sel, size_t need, std::vector<char>& buf) -> bool
        {
            if (buf.size() == need) return build_and_test(sel, buf);
            for (char c : cs) {
                buf.push_back(c);
                if (assign_chars(sel, need, buf)) return true;
                buf.pop_back();
            }
            return false;
        };

        // Try all edit combinations up to MAX_EDITS (with pruning: ≤1 char-needing edit per combo)
        int n = (int)edits.size();
        for (int k = 1; k <= MAX_EDITS; ++k) {
            std::vector<int> sel(k);
            std::function<bool(int)> search = [&](int idx) -> bool {
                if (idx == k) {
                    size_t need = 0;
                    for (int i = 0; i < k; ++i) if (needsChar(edits[sel[i]])) ++need;
                    if (need > 1) return false;
                    if (need == 0) return build_and_test(sel, {});
                    std::vector<char> buf;
                    buf.reserve(need);
                    return assign_chars(sel, need, buf);
                }
                for (int i = (idx ? sel[idx-1]+1 : 0); i < n; ++i) {
                    sel[idx] = i;
                    if (search(idx + 1)) return true;
                }
                return false;
            };
            if (search(0)) return 0;
        }

        std::cout << "No fix with up to " << MAX_EDITS << " edits found.\n";
        printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld incomplete: %lld ***\n",
               ORACLE, OK, BAD, INC);
        return 1;
    } catch (const std::exception& e) {
        ++BAD;
        std::cerr << "Unhandled exception: " << e.what() << "\n";
        printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld incomplete: %lld ***\n",
               ORACLE, OK, BAD, INC);
        return 1;
    }
    return 0;
}
