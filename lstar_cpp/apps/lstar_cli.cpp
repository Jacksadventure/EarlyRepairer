#include <fstream>
#include <iostream>
#include <optional>
#include <set>
#include <string>
#include <unordered_set>
#include <vector>

#include "lstar/DFA.hpp"
#include "lstar/LStar.hpp"
#include "lstar/ObservationTable.hpp"
#include "lstar/ValidatorOracle.hpp"

using namespace lstar;

struct Args {
  std::string positives;                 // --positives
  std::string negatives;                 // --negatives
  std::string category;                  // --category (Date/Time/URL/ISBN/IPv4/IPv6/FilePath)
  std::optional<std::string> output_dot; // --output-dot
  std::optional<std::string> output_grammar; // --output-grammar
  std::optional<std::string> oracle_validator; // --oracle-validator (override command)
};

static void print_usage(const char* argv0) {
  std::cerr
      << "Usage: " << argv0 << " --positives <file> --negatives <file> --category <Category> [--output-grammar <file>] [--output-dot <file>] [--oracle-validator <cmd>]\n"
      << "  --positives <file>         Path to positives.txt (one string per line; empty line is epsilon)\n"
      << "  --negatives <file>         Path to negatives.txt (optional but recommended)\n"
      << "  --category  <Category>     One of: Date, Time, URL, ISBN, IPv4, IPv6, FilePath (used by validators/match.py)\n"
      << "  --output-grammar <file>    Write learned right-linear grammar JSON (default: stdout)\n"
      << "  --output-dot <file>        Write learned DFA as Graphviz DOT (opt-in)\n"
      << "  --oracle-validator <cmd>   Override validator command (e.g., \"validators/regex/validate_date\")\n"
      << "Notes:\n"
      << "  Default output is grammar JSON. Membership uses validators/* or python3 match.py, like the original.\n"
      << "  The observation table is seeded with positive prefixes first, like the original pipeline.\n";
}

static std::unordered_set<std::string> read_lines_set(const std::string& path) {
  std::unordered_set<std::string> out;
  if (path.empty()) return out;
  std::ifstream in(path);
  if (!in) return out;
  std::string line;
  while (std::getline(in, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    out.insert(line);
  }
  return out;
}

int main(int argc, char** argv) {
  Args args;
  // Parse long options (accept legacy short aliases too for convenience)
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    if (a == "-h" || a == "--help") {
      print_usage(argv[0]);
      return 0;
    } else if ((a == "--positives" || a == "-p") && i + 1 < argc) {
      args.positives = argv[++i];
    } else if ((a == "--negatives" || a == "-n") && i + 1 < argc) {
      args.negatives = argv[++i];
    } else if (a == "--category" && i + 1 < argc) {
      args.category = argv[++i];
    } else if ((a == "--output-dot" || a == "-o") && i + 1 < argc) {
      args.output_dot = argv[++i];
    } else if (a == "--output-grammar" && i + 1 < argc) {
      args.output_grammar = argv[++i];
    } else if (a == "--oracle-validator" && i + 1 < argc) {
      args.oracle_validator = argv[++i];
    } else {
      std::cerr << "Unknown or incomplete argument: " << a << "\n";
      print_usage(argv[0]);
      return 1;
    }
  }

  if (args.category.empty()) {
    std::cerr << "Error: --category is required.\n";
    print_usage(argv[0]);
    return 1;
  }

  // Load datasets
  auto positives = read_lines_set(args.positives);
  auto negatives = read_lines_set(args.negatives);

  if (positives.empty() && negatives.empty()) {
    std::cerr << "Error: datasets are empty. Provide --positives/--negatives files.\n";
    return 2;
  }

  // Build alphabet like original: derive from examples, fallback to 'ab'
  std::vector<char> alphabet = derive_alphabet_from_examples(positives, negatives);

  // Build validator override command if provided
  std::vector<std::string> validator_override_cmd;
  if (args.oracle_validator.has_value()) {
    // Simple split on space (users can quote their command in shell)
    std::string cmd = *args.oracle_validator;
    std::string cur;
    for (char c : cmd) {
      if (c == ' ') {
        if (!cur.empty()) { validator_override_cmd.push_back(cur); cur.clear(); }
      } else {
        cur.push_back(c);
      }
    }
    if (!cur.empty()) validator_override_cmd.push_back(cur);
  }

  // Construct validator-backed oracle (membership via validators/match.py)
  ValidatorOracle oracle(args.category, positives, negatives, validator_override_cmd, /*check_negatives=*/true);

  // Observation table with derived alphabet
  ObservationTable table(alphabet);

  // Seed with all positives (the learner will add all prefixes)
  std::vector<std::string> seed(positives.begin(), positives.end());

  // Learn DFA (L*)
  DFA dfa = LStarLearner::learn(table, oracle, seed);

  // Export outputs (default: grammar JSON)
  std::string grammar_json = dfa.to_right_linear_json(table.A());
  if (args.output_grammar) {
    std::ofstream out(*args.output_grammar);
    if (!out) {
      std::cerr << "Error: cannot open output file: " << *args.output_grammar << "\n";
      return 3;
    }
    out << grammar_json;
  } else if (args.output_dot) {
    std::string dot = dfa.to_dot(table.A());
    std::ofstream out(*args.output_dot);
    if (!out) {
      std::cerr << "Error: cannot open output file: " << *args.output_dot << "\n";
      return 3;
    }
    out << dot;
  } else {
    std::cout << grammar_json;
  }

  return 0;
}
