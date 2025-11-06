#pragma once

#include <string>
#include <utility>
#include <vector>

#include "lstar/DFA.hpp"
#include "lstar/ObservationTable.hpp"

namespace lstar {

// Orchestrates the L* learning loop using an ObservationTable and an Oracle.
class LStarLearner {
public:
  // Runs L* and returns the learned DFA. The loop terminates when the oracle
  // reports equivalence (dataset- or regex-based).
  // Optional seed_prefixes will be added as access strings (all prefixes) after init,
  // mirroring the original pipeline that biases with positives first.
  static DFA learn(ObservationTable& T, Oracle& oracle, const std::vector<std::string>& seed_prefixes = {}) {
    // Initialize table with epsilon and extend
    T.init_table(oracle);
    // Seed with provided prefixes (and all of their prefixes)
    for (const auto& s : seed_prefixes) {
      for (size_t i = 1; i <= s.size(); ++i) {
        T.add_prefix(s.substr(0, i), oracle);
      }
    }

    // Main loop
    while (true) {
      // Maintain closedness and consistency
      while (true) {
        auto [is_closed, t] = T.closed();
        auto [is_consistent, a_plus_s] = T.consistent();
        if (is_closed && is_consistent) break;

        if (!is_closed) {
          T.add_prefix(t, oracle);
          continue; // re-check after mutation
        }
        if (!is_consistent) {
          T.add_suffix(a_plus_s, oracle);
          continue; // re-check after mutation
        }
      }

      // Build hypothesis DFA
      DFA dfa = T.to_dfa();

      // Ask oracle for (approximate) equivalence, and potential counterexample
      auto [eq, counterexample] = oracle.is_equivalent(dfa, T.A());
      if (eq) return dfa;

      // Add all prefixes of the counterexample to P, to refine the table
      for (size_t i = 1; i <= counterexample.size(); ++i) {
        T.add_prefix(counterexample.substr(0, i), oracle);
      }
    }
  }
};

} // namespace lstar
