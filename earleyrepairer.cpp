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
    auto begin() const { return s_.begin(); }
    auto end()   const { return s_.end();   }
};

/*────────────────── Grammar basics ──────────────*/
const std::string Any   = "$.";        // wildcard
const std::string Empty = "<$>";       // global ε ─ still used internally

using RuleMap = std::map<std::string,
                         std::vector<std::vector<std::string>>>;

struct Grammar {
    RuleMap R;

    void add(const std::string& lhs,
             std::vector<std::string> rhs)
    { R[lhs].push_back(std::move(rhs)); }

    /*—— covering grammar (Aho et al.) with per-terminal delete NTs ——*/
    Grammar covering() const {
        Grammar g = *this;
        g.add(Empty, {});                               // global ε

        for (auto const& [lhs, rhss] : R)
            for (auto const& rhs : rhss) {
                std::vector<std::string> nr;
                for (size_t pos = 0; pos < rhs.size(); ++pos) {
                    const std::string& sym = rhs[pos];
                    nr.push_back(Any);                  // insertion before sym

                    if (!R.count(sym)) {                // terminal
                        // Position-unique boxes so each character can be edited independently
                        std::string tag = lhs + ":" + std::to_string(pos);
                        std::string box = "<$[" + tag + "]>";
                        std::string del = "<$del[" + tag + "]>";
                        std::string neg = "<$![" + tag + "]>";
                        g.add(box, {sym});              // match
                        g.add(box, {del});              // delete
                        g.add(box, {Any, sym});      // insert 
                        // g.add(box, {neg});           // substitute (neg-class)
                        g.add(neg, {});                 // any≠sym

                        nr.push_back(std::move(box));
                    } else {                            // non-terminal
                        nr.push_back(sym);
                    }
                }
                nr.push_back(Any);                      // insertion after sym
                g.add(lhs, std::move(nr));
            }
        return g;
    }

    /*—— build grammar from raw string — each char gets its own NT ——*/
    static Grammar fromString(const std::string& str,
                              const std::string& start = "<start>")
    {
        Grammar g;
        std::vector<std::string> start_rhs;
        std::size_t idx = 0;

        for (char c : str) {
            std::string nt = "<c" + std::to_string(idx++) + ">";
            start_rhs.push_back(nt);
            g.add(nt, {std::string(1, c)});            // nt → 'c'
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

/* Multi-edit support: apply up to K edits in one derivation */
struct EditApp {
    const Prod* p = nullptr;
    bool applied = false;
    bool char_used = false;
    char ch = 0;
    bool needChar = false;
};

std::string gen_multi(const std::string& sym,
                      const RuleMap& base, const RuleMap& cov,
                      std::vector<EditApp>& apps, int active)
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
            if (a.ch) { a.char_used = true; return std::string(1, a.ch); }
        }
        return "";
    }
    if (sym.rfind("<$del[", 0) == 0) {
        return "";
    }
    if (!cov.count(sym))
        return sym == "\0" ? "" : sym;

    // If not inside an active edit subtree, see if an unapplied edit targets this symbol
    if (active == -1) {
        for (size_t i = 0; i < apps.size(); ++i) {
            auto& a = apps[i];
            if (!a.applied && sym == a.p->lhs) {
                a.applied = true;
                std::string out;
                for (auto const& s : a.p->rhs)
                    out += gen_multi(s, base, cov, apps, int(i));
                return out;
            }
        }
    }

    // Default expansion
    const std::vector<std::string>* rhs;
    if (base.count(sym)) rhs = &cov.at(sym).at(1);
    else                 rhs = &cov.at(sym).at(0);

    std::string out;
    for (auto const& s : *rhs)
        out += gen_multi(s, base, cov, apps, active);
    return out;
}

/*────────────────── oracle wrapper ───────────────*/
enum class Res { OK, ERR, INC };

std::string tmpFile() {
    char p[] = "/tmp/repairXXXXXX";
    int fd = mkstemp(p); if (fd == -1) throw std::runtime_error("tmp");
    close(fd); return p;
}
std::function<Res(const std::string&)> oracleWrap(const std::string& exe)
{
    return [exe](const std::string& in) -> Res {
        if (ORACLE >= MAX_ORACLE) {
            return Res::ERR;
        }
        std::string f = tmpFile(); { std::ofstream(f) << in; }
        ++ORACLE;
        std::cout << "Oracle call " << ORACLE << ": " << in << "\n";
        pid_t pid = fork();
        if (pid == -1) {
            std::remove(f.c_str());
            ++BAD;
            return Res::ERR;
        }

        if (pid == 0) {
            // Child: redirect stdout/stderr to /dev/null and exec the oracle directly (no shell).
            int devnull = open("/dev/null", O_WRONLY);
            if (devnull >= 0) {
                dup2(devnull, STDOUT_FILENO);
                dup2(devnull, STDERR_FILENO);
                close(devnull);
            }
            execl(exe.c_str(), exe.c_str(), f.c_str(), (char*)nullptr);
            _exit(127); // exec failed
        }

        int st = 0;
        auto start = std::chrono::steady_clock::now();
        const int timeout_ms = 1000;
        while (true) {
            pid_t res = waitpid(pid, &st, WNOHANG);
            if (res == -1) {
                std::remove(f.c_str());
                ++BAD;
                return Res::ERR;
            }
            if (res > 0) break;
            auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                               std::chrono::steady_clock::now() - start).count();
            if (elapsed >= timeout_ms) {
                kill(pid, SIGKILL);
                waitpid(pid, &st, 0);
                std::remove(f.c_str());
                ++BAD;
                return Res::ERR;
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
            ++BAD;
            return Res::ERR;
        } else {
            ++BAD;
            return Res::ERR;
        }
    };
}

/*────────────────── main ─────────────────────────*/
int main(int argc, char* argv[])
{
    const int MAX_EDITS = 5; // Increased edit limit from 3 to 5

    if (argc < 4) {
        std::cerr << "Usage: " << argv[0]
                  << " <parser_path> <input_string> <output_file>\n";
        return 1;
    }
    const std::string exe   = argv[1];
    const std::string inputArg = argv[2];
    const std::string outF  = argv[3];

    if (access(exe.c_str(), X_OK) != 0) {
        std::cerr << "Parser executable not found or not executable: " << exe << "\n";
        return 1;
    }

    // Allow argv[2] to be either a literal string or a path to a file.
    // If it looks like a readable file, load its contents; otherwise, treat it as the input string.
    std::string input;
    {
        std::ifstream fin(inputArg);
        if (fin.good()) {
            input.assign((std::istreambuf_iterator<char>(fin)), std::istreambuf_iterator<char>());
        } else {
            input = inputArg;
        }
    }

    auto oracle  = oracleWrap(exe);


    Grammar base = Grammar::fromString(input);
    Grammar cov  = base.covering();

    /* 0-edit quick check */
    if (oracle(input) == Res::OK) {
        std::ofstream(outF) << input;
        std::cout << "Repaired string: " << input << "\n";
        printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld incomplete: %lld ***\n",
               ORACLE, OK, BAD, INC);
        return 0;
    }

    /* collect all single-edit productions */
    std::vector<Prod> edits;                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          
    for (auto const& [lhs, rhss] : cov.R)
        for (auto const& rhs : rhss) {
            bool ins = !rhs.empty() && rhs[0] == Any;
            bool del = rhs.size()==1 && rhs[0].rfind("<$del[",0)==0;
            bool sub = rhs.size()==1 && rhs[0].rfind("<$![",0)==0;
            if (ins || del || sub) edits.push_back({lhs, rhs});
        }

    CharSet cs;


    // Cached oracle to avoid duplicate work
    std::unordered_set<std::string> seen;
    auto oracle_cached = [&](const std::string& s) -> Res {
        if (seen.insert(s).second) return oracle(s);
        return Res::ERR;
    };

    // Multi-edit search with pruning and budgets
    {

        auto needsChar = [&](const Prod& p) -> bool {
            return (!p.rhs.empty() && p.rhs[0] == Any) ||
                   (p.rhs.size() == 1 && p.rhs[0].rfind("<$![", 0) == 0);
        };

        // Build and test a candidate given a selection of edit indices and chars
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
            std::string cand = gen_multi("<start>", base.R, cov.R, apps, -1);
            // ensure all selected edits actually applied
            for (auto const& a : apps) if (!a.applied) return false;
            if (oracle_cached(cand) == Res::OK) {
                std::ofstream(outF) << cand;
                std::cout << "Repaired string: " << cand << "\n";
                printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld incomplete: %lld ***\n",
                       ORACLE, OK, BAD, INC);
                return true;
            }
            return false;
        };

        // Assign exactly 'need' chars (we will only ever request <= 1 to prevent explosion)
        std::function<bool(const std::vector<int>&, size_t, std::vector<char>&)> assign_chars =
        [&](const std::vector<int>& sel, size_t need, std::vector<char>& buf) -> bool
        {
            if (buf.size() == need) {
                return build_and_test(sel, buf);
            }
            for (char c : cs) {
                buf.push_back(c);
                if (assign_chars(sel, need, buf)) return true;
                buf.pop_back();
            }
            return false;
        };

        // Partition edits into deletions and insertions
        std::vector<int> del_idx, ins_idx, other_idx;
        for (int i = 0; i < (int)edits.size(); ++i) {
            const auto& rhs = edits[i].rhs;
            bool ins = !rhs.empty() && rhs[0] == Any;
            bool del = rhs.size()==1 && rhs[0].rfind("<$del[",0)==0;
            bool sub = rhs.size()==1 && rhs[0].rfind("<$![",0)==0;
            if (del) del_idx.push_back(i);
            else if (ins) ins_idx.push_back(i);
            else if (sub) other_idx.push_back(i);
        }

        // Try all edit combinations up to MAX_EDITS
        int n = (int)edits.size();
        for (int k = 1; k <= MAX_EDITS; ++k) {
            // Use std::vector<int> to hold indices of selected edits
            std::vector<int> sel(k);
            std::function<bool(int, int)> search;
            search = [&](int idx, int start) -> bool {
                if (idx == k) {
                    // Prune: allow at most one char insertion/substitution per combination
                    size_t need = 0;
                    for (int i = 0; i < k; ++i) {
                        if (needsChar(edits[sel[i]])) ++need;
                    }
                    if (need > 1) return false;
                    if (need == 0) {
                        if (build_and_test(sel, {})) return true;
                    } else {
                        std::vector<char> buf;
                        if (assign_chars(sel, need, buf)) return true;
                    }
                    return false;
                }
                for (int i = (idx == 0 ? 0 : sel[idx - 1] + 1); i < n; ++i) {
                    sel[idx] = i;
                    if (search(idx + 1, i + 1)) return true;
                }
                return false;
            };
            if (search(0, 0)) return 0;
        }
    }

    std::cout << "No fix with up to " << MAX_EDITS << " edits found.\n";
    printf("*** Number of required oracle runs: %lld correct: %lld incorrect: %lld incomplete: %lld ***\n",
           ORACLE, OK, BAD, INC);
    return 1;
}
