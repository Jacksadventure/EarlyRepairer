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

/*────────────────── Statistics ──────────────────*/
static long long ORACLE = 0, OK = 0, BAD = 0, INC = 0;
static long long MAX_ORACLE = (long long)1e18;

/*────────────────── Character set ───────────────*/
class CharSet {
    std::set<char> s_;
public:
    CharSet() { reset(); }
    void reset() {
        s_.clear();
        for (int c = 33; c <= 126; ++c) s_.insert(char(c));
        s_.insert('\n'); s_.insert('\t');
    }
    void setAllowed(const std::string& chars) {
        s_.clear();
        for (char c : chars) s_.insert(c);
    }
    auto begin() const { return s_.begin(); }
    auto end()   const { return s_.end();   }
};

/*────────────────── Grammar basics ───────────────*/
const std::string Any   = "$.";        // wildcard terminal for insert-before
const std::string Empty = "<$>";       // global ε (not used as a rule key here)

using RuleMap = std::map<std::string,
                         std::vector<std::vector<std::string>>>;

struct Grammar {
    RuleMap R;

    void add(const std::string& lhs, std::vector<std::string> rhs) {
        R[lhs].push_back(std::move(rhs));
    }

    // Covering grammar:
    // For rules of the form <cK> → t (t is a single terminal), produce:
    //   <cK> → t | <$del[t]> | $. t | <$![t]>
    // For other rules (e.g., <start> → <c0> <c1> … <cN>), copy as-is.
    // The sentinel production t == "\0" becomes ε (only).
    Grammar covering() const {
        Grammar cg;

        for (const auto& [lhs, rhss] : R) {
            for (const auto& rhs : rhss) {
                // Single-terminal rule: expand to 4-alternative cover
                if (rhs.size() == 1 && !R.count(rhs[0])) {
                    const std::string& t = rhs[0];
                    if (t == "\0") {
                        // Sentinel → ε
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
                    // Structural rule: keep as-is (e.g., <start> sequence)
                    cg.add(lhs, rhs);
                }
            }
        }
        return cg;
    }

    // Build base grammar from a raw string:
    // <start> → <c0> <c1> ... <cN>   and
    // <cK> → 'char', plus a sentinel <cN> → "\0"
    static Grammar fromString(const std::string& str,
                              const std::string& start = "<start>")
    {
        Grammar g;
        std::vector<std::string> start_rhs;
        std::size_t idx = 0;

        for (char c : str) {
            std::string nt = "<c" + std::to_string(idx++) + ">";
            start_rhs.push_back(nt);
            g.add(nt, {std::string(1, c)});  // <cK> → 'c'
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

/* One selected edit application */
struct EditApp {
    const Prod* p = nullptr;
    bool applied = false;
    bool char_used = false;
    char ch = 0;          // candidate character (for $. or <$![...]>)
    bool needChar = false;
};

/*──────── String generation for covering grammar ────────
   - "$."           : outputs one char only if inside an active edit; otherwise "".
   - "<$![...]> "   : terminal; consumes one char only in active edit; otherwise "".
   - "<$del[...]> " : delete token → "".
   - "\0"           : suppressed (ε) when seen as terminal.
   - Nonterminals   : if there is an unapplied edit with LHS==sym, expand that RHS under that edit;
                      otherwise use FIRST production (assumed "match" branch).
*/
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

    // Terminal? (no production in covering grammar)
    auto it = cov.find(sym);
    if (it == cov.end()) {
        return sym == "\0" ? "" : sym;
    }

    // If not inside an active edit subtree, see if an unapplied edit targets this symbol
    if (active == -1) {
        for (size_t i = 0; i < apps.size(); ++i) {
            auto& a = apps[i];
            if (!a.applied && sym == a.p->lhs) {
                a.applied = true;
                std::string out;
                for (const auto& s : a.p->rhs)
                    out += gen_multi(s, cov, apps, int(i));
                return out;
            }
        }
    }

    // Default expansion: FIRST production = "match" branch
    const auto& first_rhs = it->second.front();
    std::string out;
    for (const auto& s : first_rhs)
        out += gen_multi(s, cov, apps, active);
    return out;
}



/*────────────────── oracle wrapper ───────────────*/
enum class Res { OK, ERR, INC };

static std::string tmpFile() {
    char p[] = "/tmp/repairXXXXXX";
    int fd = mkstemp(p); if (fd == -1) throw std::runtime_error("tmp");
    close(fd); return p;
}
static std::function<Res(const std::string&)> oracleWrap(const std::string& exe)
{
    return [exe](const std::string& in) -> Res {
        if (ORACLE >= MAX_ORACLE) return Res::ERR;
        std::cout << "Oracle called:" << std::endl;
        std::cout << in << std::endl;
        std::string f = tmpFile(); { std::ofstream(f) << in; }
        ++ORACLE;

        // readable logging
        auto show = [&](const std::string& s) {
            std::string out; out.reserve(std::min<size_t>(s.size(), 120));
            for (unsigned char ch : s) {
                if (ch == '\n') out += "\\n";
                else if (ch == '\t') out += "\\t";
                else if (ch < 32 || ch == 127) {
                    char buf[6]; snprintf(buf, sizeof(buf), "\\x%02X", ch);
                    out += buf;
                } else out.push_back(ch);
            }
            if (s.size() > 120) out += "…";
            return out.empty() ? std::string("<EMPTY>") : out;
        };

        pid_t pid = fork();
        if (pid == -1) { std::remove(f.c_str()); ++BAD; return Res::ERR; }

        if (pid == 0) {
            int devnull = open("/dev/null", O_WRONLY);
            if (devnull >= 0) {
                dup2(devnull, STDOUT_FILENO);
                dup2(devnull, STDERR_FILENO);
                close(devnull);
            }
            execl(exe.c_str(), exe.c_str(), f.c_str(), (char*)nullptr);
            _exit(127);
        }

        int st = 0;
        auto start = std::chrono::steady_clock::now();
        int timeout_ms = 6000;
        if (const char* env = std::getenv("REPAIR_VALIDATOR_TIMEOUT_MS")) {
            int v = std::atoi(env);
            if (v > 0 && v <= 60000) timeout_ms = v;
        }
        while (true) {
            pid_t res = waitpid(pid, &st, WNOHANG);
            if (res == -1) { std::remove(f.c_str()); ++BAD; return Res::ERR; }
            if (res > 0) break;
            auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                               std::chrono::steady_clock::now() - start).count();
            if (elapsed >= timeout_ms) {
                kill(pid, SIGKILL);
                waitpid(pid, &st, 0);
                std::remove(f.c_str());
                ++BAD; return Res::ERR;
            }
            usleep(5000);
        }
        std::remove(f.c_str());

        if (WIFEXITED(st)) {
            switch (WEXITSTATUS(st)) {
                case 0:   ++OK;  return Res::OK;
                case 1:   ++BAD; return Res::ERR;
                case 255: ++INC; return Res::INC;
                default:  ++BAD; return Res::ERR;
            }
        } else if (WIFSIGNALED(st)) {
            ++BAD; return Res::ERR;
        } else {
            ++BAD; return Res::ERR;
        }
    };
}

/*────────────────── main ─────────────────────────*/
int main(int argc, char* argv[])
{
    int MAX_EDITS = 5;

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
            std::ifstream fin(inputArg);
            if (fin.good()) {
                input.assign((std::istreambuf_iterator<char>(fin)), std::istreambuf_iterator<char>());
            } else {
                input = inputArg;
            }
        }

        // Allow overriding max edits via environment variable
        if (const char* env_edits = std::getenv("REPAIR_MAX_EDITS")) {
            int v = std::atoi(env_edits);
            if (v >= 1 && v <= 10) MAX_EDITS = v;
        }

        auto oracle  = oracleWrap(exe);

        Grammar base = Grammar::fromString(input);
        Grammar cov  = base.covering();

        /* 0-edit quick check */
        if (oracle(input) == Res::OK) {
            std::ofstream(outF) << input;
            std::cout << "Repaired string: " << input << "\n";
            printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld \n",
                   ORACLE, OK, BAD);
            return 0;
        }

        /* collect all single-edit productions (insert/delete/substitute) */
        std::vector<Prod> edits;
        for (const auto& [lhs, rhss] : cov.R) {
            for (const auto& rhs : rhss) {
                bool is_insert = (!rhs.empty() && rhs[0] == Any);                   // $. t
                bool is_delete = (rhs.size()==1 && rhs[0].rfind("<$del[", 0) == 0); // <$del[t]>
                bool is_subst  = (rhs.size()==1 && rhs[0].rfind("<$![",  0) == 0);  // <$![t]>
                if (is_insert || is_delete || is_subst) edits.push_back({lhs, rhs});
            }
        }

        CharSet cs;
        // Cached oracle to avoid duplicate work
        std::unordered_set<std::string> seen;
        auto oracle_cached = [&](const std::string& s) -> Res {
            if (seen.insert(s).second) return oracle(s);
            return Res::ERR;
        };

        auto needsChar = [&](const Prod& p) -> bool {
            return (!p.rhs.empty() && p.rhs[0] == Any) ||                            // insert
                   (p.rhs.size()==1 && p.rhs[0].rfind("<$![", 0) == 0);              // substitute
        };

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
            std::string cand = gen_multi("<start>", cov.R, apps, -1);
            for (const auto& a : apps) if (!a.applied) return false; // must be used
            if (oracle_cached(cand) == Res::OK) {
                std::ofstream(outF) << cand;
                std::cout << "Repaired string: " << cand << "\n";
                printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld \n",
                       ORACLE, OK, BAD);
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
        printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld \n",
               ORACLE, OK, BAD);
        return 1;
    } catch (const std::exception& e) {
        ++BAD;
        std::cerr << "Unhandled exception: " << e.what() << "\n";
        printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld \n",
               ORACLE, OK, BAD);
        return 1;
    }
}
