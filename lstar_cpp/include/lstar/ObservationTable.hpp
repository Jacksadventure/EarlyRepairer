#pragma once

#include <algorithm>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "lstar/DFA.hpp"

namespace lstar {

// Oracle interface (membership/equivalence)
class Oracle {
public:
  virtual ~Oracle() = default;
  // Return 1 if member of target language, 0 otherwise.
  virtual int is_member(const std::string& q) = 0;
  // Check hypothesis DFA against target. If equivalent, return {true, ""}.
  // Otherwise return {false, counterexample}.
  virtual std::pair<bool, std::string> is_equivalent(const DFA& dfa,
                                                     const std::vector<char>& alphabet) = 0;
};

// Observation table for Angluin's L* algorithm.
// Rows (P) and columns (S) are over strings; A is the alphabet.
class ObservationTable {
public:
  explicit ObservationTable(std::vector<char> alphabet)
      : P_{""}, S_{""}, A_{std::move(alphabet)} {}

  // Initialize with epsilon query and populate table.
  void init_table(Oracle& oracle) {
    T_[""][""] = oracle.is_member("");
    update_table(oracle);
  }

  // Update cells for all p in (P union P·A) and all s in S, performing membership queries.
  void update_table(Oracle& oracle) {
    // Unique union of P and P·A
    std::vector<std::string> rows = P_;
    rows.reserve(P_.size() * (1 + A_.size()));
    for (const auto& p : P_) {
      for (char a : A_) rows.push_back(p + a);
    }
    // Deduplicate
    std::unordered_set<std::string> seen;
    std::vector<std::string> PuPxA;
    PuPxA.reserve(rows.size());
    for (auto& r : rows) {
      if (seen.insert(r).second) PuPxA.push_back(std::move(r));
    }

    for (const auto& p : PuPxA) {
      auto& row = T_[p]; // creates row if not exists
      for (const auto& s : S_) {
        if (row.find(s) != row.end()) continue;
        row[s] = oracle.is_member(p + s);
      }
    }
  }

  // Closedness: for each t in P·A, state(t) must appear as some state(p) for p in P.
  // Returns {true, ""} if closed, else {false, offending_prefix_t}.
  std::pair<bool, std::string> closed() const {
    std::unordered_set<std::string> states_in_P;
    states_in_P.reserve(P_.size());
    for (const auto& p : P_) states_in_P.insert(state(p));

    for (const auto& p : P_) {
      for (char a : A_) {
        std::string t = p + a;
        if (!has_row(t)) continue; // should exist if update_table called; guard anyway
        if (states_in_P.find(state(t)) == states_in_P.end()) {
          return {false, t};
        }
      }
    }
    return {true, ""};
  }

  // Consistency: if state(p1) == state(p2) then for all a in A, state(p1a) == state(p2a).
  // If inconsistent, returns {false, a_plus_suffix_to_add}.
  // We compute a+suffix that distinguishes the mismatched successor rows.
  std::pair<bool, std::string> consistent() const {
    // Find pairs of rows with identical state id
    for (size_t i = 0; i < P_.size(); ++i) {
      for (size_t j = i + 1; j < P_.size(); ++j) {
        const auto& p1 = P_[i];
        const auto& p2 = P_[j];
        if (state(p1) != state(p2)) continue;

        // Check successor rows under all a in A
        for (char a : A_) {
          const auto& r1 = row(p1 + a);
          const auto& r2 = row(p2 + a);
          // If rows differ in any suffix s, add a+s as new suffix
          for (const auto& s : S_) {
            auto v1 = r1.at(s);
            auto v2 = r2.at(s);
            if (v1 != v2) {
              return {false, std::string(1, a) + s};
            }
          }
        }
      }
    }
    return {true, ""};
  }

  // Add a new prefix to P and update table.
  void add_prefix(const std::string& p, Oracle& oracle) {
    if (std::find(P_.begin(), P_.end(), p) != P_.end()) return;
    P_.push_back(p);
    update_table(oracle);
  }

  // Add a new suffix to S and update table.
  void add_suffix(const std::string& sfx, Oracle& oracle) {
    if (std::find(S_.begin(), S_.end(), sfx) != S_.end()) return;
    S_.push_back(sfx);
    update_table(oracle);
  }

  // Build a DFA from the current table.
  DFA to_dfa() const {
    // Map state id -> representative row prefix
    std::unordered_map<std::string, std::string> rep;
    rep.reserve(P_.size());
    for (const auto& p : P_) {
      std::string sid = state(p);
      if (rep.find(sid) == rep.end()) rep.emplace(sid, p);
    }

    DFA dfa;
    // Start state
    dfa.set_start(state(""));

    // Add states and transitions
    for (const auto& kv : rep) {
      const auto& sid = kv.first;
      const auto& p = kv.second;
      bool accepting = cell(p, "") == 1;
      dfa.add_state(sid, accepting);
    }
    // Transitions
    for (const auto& kv : rep) {
      const auto& sid_from = kv.first;
      const auto& p = kv.second;
      for (char a : A_) {
        std::string sid_to = state(p + a);
        dfa.add_transition(sid_from, a, sid_to);
      }
    }

    return dfa;
  }

  // Accessors
  const std::vector<std::string>& P() const { return P_; }
  const std::vector<std::string>& S() const { return S_; }
  const std::vector<char>& A() const { return A_; }

private:
  // Internal table: row prefix -> {suffix -> membership}
  std::unordered_map<std::string, std::unordered_map<std::string, int>> T_;
  std::vector<std::string> P_; // prefixes (prefix-closed)
  std::vector<std::string> S_; // suffixes (suffix-closed)
  std::vector<char> A_;        // alphabet

  bool has_row(const std::string& p) const {
    return T_.find(p) != T_.end();
  }

  const std::unordered_map<std::string, int>& row(const std::string& p) const {
    return T_.at(p);
  }

  int cell(const std::string& p, const std::string& s) const {
    return T_.at(p).at(s);
  }

  // State identifier is the pattern of 1/0 over S for row p
  std::string state(const std::string& p) const {
    std::string id;
    id.reserve(S_.size() + 2);
    id.push_back('<');
    for (const auto& s : S_) {
      auto itp = T_.find(p);
      if (itp == T_.end()) { id.push_back('0'); continue; } // Should not happen if updated
      auto its = itp->second.find(s);
      int v = (its == itp->second.end()) ? 0 : its->second;
      id.push_back(static_cast<char>('0' + (v ? 1 : 0)));
    }
    id.push_back('>');
    return id;
  }
};

} // namespace lstar
