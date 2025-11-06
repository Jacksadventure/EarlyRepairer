#pragma once

#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <sstream>

namespace lstar {

// Simple DFA representation built from an observation table.
class DFA {
public:
  // State identifier, e.g., "<0101>"
  using State = std::string;

  DFA() = default;

  // Add a state. If accepting==true, mark as accepting.
  void add_state(const State& s, bool accepting) {
    states_.insert(s);
    if (accepting) accept_.insert(s);
  }

  // Set start state.
  void set_start(const State& s) { start_ = s; states_.insert(s); }

  // Add a transition: from state 'from' with symbol 'a' to state 'to'.
  void add_transition(const State& from, char a, const State& to) {
    states_.insert(from);
    states_.insert(to);
    delta_[from][a] = to;
  }

  // Run DFA on input word. Returns true if accepted.
  bool accepts(const std::string& word) const {
    if (start_.empty()) return false;
    State cur = start_;
    for (char c : word) {
      auto it1 = delta_.find(cur);
      if (it1 == delta_.end()) return false;
      auto it2 = it1->second.find(c);
      if (it2 == it1->second.end()) return false;
      cur = it2->second;
    }
    return accept_.find(cur) != accept_.end();
  }

  // Export to Graphviz DOT format
  std::string to_dot(const std::vector<char>& alphabet) const {
    std::ostringstream os;
    os << "digraph DFA {\n";
    os << "  rankdir=LR;\n";
    os << "  node [shape=circle];\n";
    // Invisible start arrow
    os << "  __start [shape=point];\n";
    if (!start_.empty()) os << "  __start -> \"" << escape(start_) << "\";\n";

    // Accepting states as doublecircle
    for (const auto& s : states_) {
      bool acc = accept_.count(s) > 0;
      os << "  \"" << escape(s) << "\""
         << " [shape=" << (acc ? "doublecircle" : "circle") << "];\n";
    }
    // Transitions
    for (const auto& [from, mp] : delta_) {
      for (const auto& [c, to] : mp) {
        os << "  \"" << escape(from) << "\" -> \"" << escape(to)
           << "\" [label=\"" << escape_label(c) << "\"];\n";
      }
    }
    os << "}\n";
    return os.str();
  }

  // Export equivalent right-linear grammar as JSON:
  // {
  //   "start_sym": "<state>",
  //   "alphabet": ["a","b",...],
  //   "grammar": {
  //     "<state>": [["a","<next>"], [], ...] // [] denotes epsilon
  //   }
  // }
  std::string to_right_linear_json(const std::vector<char>& alphabet) const {
    std::ostringstream os;
    os << "{\n";
    os << "  \"start_sym\": " << "\"" << json_escape(start_) << "\",\n";
    os << "  \"alphabet\": [";
    for (size_t i = 0; i < alphabet.size(); ++i) {
      if (i) os << ", ";
      std::string t(1, alphabet[i]);
      os << "\"" << json_escape(t) << "\"";
    }
    os << "],\n";
    os << "  \"grammar\": {\n";
    // For stable-ish output, we will iterate over states_ insertion order is not stable,
    // but this is fine for functional use. We emit transitions and epsilon for accepting states.
    bool first_nt = true;
    for (const auto& s : states_) {
      if (!first_nt) os << ",\n";
      first_nt = false;
      os << "    " << "\"" << json_escape(s) << "\": [";
      bool first_prod = true;

      // Productions from transitions: for each (a -> to)
      auto it_from = delta_.find(s);
      if (it_from != delta_.end()) {
        for (const auto& kv : it_from->second) {
          char a = kv.first;
          const auto& to = kv.second;
          if (!first_prod) os << ", ";
          first_prod = false;
          std::string a_str(1, a);
          os << "[\"" << json_escape(a_str) << "\", \"" << json_escape(to) << "\"]";
        }
      }
      // Epsilon if accepting
      if (accept_.count(s) > 0) {
        if (!first_prod) os << ", ";
        os << "[]";
      }
      os << "]";
    }
    os << "\n  }\n";
    os << "}\n";
    return os.str();
  }

  const State& start() const { return start_; }
  const std::unordered_set<State>& states() const { return states_; }
  const std::unordered_set<State>& accepting_states() const { return accept_; }

private:
  // Transition function: delta[state][symbol] = next_state
  std::unordered_map<State, std::unordered_map<char, State>> delta_;
  std::unordered_set<State> states_;
  std::unordered_set<State> accept_;
  State start_;

  static std::string escape(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
      if (c == '"' || c == '\\') out.push_back('\\');
      out.push_back(c);
    }
    return out;
  }

  static std::string escape_label(char c) {
    if (c == '"' || c == '\\') return std::string("\\") + c;
    if (c == '\n') return "\\n";
    if (c == '\t') return "\\t";
    return std::string(1, c);
  }

  static std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 2);
    for (unsigned char c : s) {
      switch (c) {
        case '\"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
          if (c < 0x20) {
            char buf[7];
            std::snprintf(buf, sizeof(buf), "\\u%04x", c);
            out += buf;
          } else {
            out.push_back(static_cast<char>(c));
          }
      }
    }
    return out;
  }
};

} // namespace lstar
