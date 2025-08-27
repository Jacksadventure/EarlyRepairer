#include <iostream>
#include <map>
#include <string>
#include <utility>
#include <vector>

const std::string Any   = "$.";   // wildcard for insert-before
const std::string Empty = "<$>";

using RuleMap = std::map<std::string, std::vector<std::vector<std::string>>>;

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

    static void print(const Grammar& g) {
        for (const auto& [lhs, rhss] : g.R) {
            std::cout << lhs << " → ";
            for (size_t i = 0; i < rhss.size(); ++i) {
                const auto& rhs = rhss[i];
                if (rhs.empty()) std::cout << "ε";
                else {
                    for (size_t j = 0; j < rhs.size(); ++j) {
                        std::cout << rhs[j];
                        if (j + 1 < rhs.size()) std::cout << ' ';
                    }
                }
                if (i + 1 < rhss.size()) std::cout << " | ";
            }
            std::cout << '\n';
        }
    }
};

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <input_string>\n";
        return 1;
    }
    const std::string input = argv[1];

    Grammar base = Grammar::fromString(input);
    Grammar cov  = base.covering();

    std::cout << "Covering Grammar:\n";
    Grammar::print(cov);
    return 0;
}