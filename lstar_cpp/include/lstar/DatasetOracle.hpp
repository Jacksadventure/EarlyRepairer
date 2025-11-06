#pragma once

#include <algorithm>
#include <fstream>
#include <iostream>
#include <set>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

#include "lstar/DFA.hpp"
#include "lstar/ObservationTable.hpp"

namespace lstar {

// Oracle backed by labeled datasets (positives and negatives).
// - membership: returns 1 if string is in positives; 0 if in negatives or unknown
// - equivalence: checks hypothesis DFA against all samples; returns first counterexample found
class DatasetOracle : public Oracle {
public:
  DatasetOracle(std::unordered_set<std::string> positives,
                std::unordered_set<std::string> negatives,
                bool default_negative = true)
      : positives_(std::move(positives)),
        negatives_(std::move(negatives)),
        default_negative_(default_negative) {}

  // Convenience constructor from file paths
  static DatasetOracle from_files(const std::string& positives_path,
                                  const std::string& negatives_path,
                                  bool default_negative = true) {
    std::unordered_set<std::string> pos = read_lines_set(positives_path);
    std::unordered_set<std::string> neg = read_lines_set(negatives_path);
    return DatasetOracle(std::move(pos), std::move(neg), default_negative);
  }

  // Compute alphabet as set of unique chars from both datasets (sorted, deduped)
  static std::vector<char> infer_alphabet(const std::unordered_set<std::string>& positives,
                                          const std::unordered_set<std::string>& negatives) {
    std::set<char> alpha;
    auto add = [&](const std::string& s) {
      for (unsigned char c : s) alpha.insert(static_cast<char>(c));
    };
    for (const auto& s : positives) add(s);
    for (const auto& s : negatives) add(s);

    // Ensure epsilon behavior via empty string exists in dataset; alphabet may be empty legitimately.
    std::vector<char> out;
    out.reserve(alpha.size());
    for (char c : alpha) out.push_back(c);
    return out;
  }

  // Oracle interface
  int is_member(const std::string& q) override {
    if (positives_.find(q) != positives_.end()) return 1;
    if (negatives_.find(q) != negatives_.end()) return 0;
    return default_negative_ ? 0 : 1;
  }

  std::pair<bool, std::string> is_equivalent(const DFA& dfa,
                                             const std::vector<char>& /*alphabet*/) override {
    // Check all labeled samples; return first mismatch as counterexample
    for (const auto& s : positives_) {
      if (!dfa.accepts(s)) return {false, s};
    }
    for (const auto& s : negatives_) {
      if (dfa.accepts(s)) return {false, s};
    }
    return {true, ""};
  }

  // Access underlying datasets
  const std::unordered_set<std::string>& positives() const { return positives_; }
  const std::unordered_set<std::string>& negatives() const { return negatives_; }

private:
  std::unordered_set<std::string> positives_;
  std::unordered_set<std::string> negatives_;
  bool default_negative_{true};

  static std::unordered_set<std::string> read_lines_set(const std::string& path) {
    std::unordered_set<std::string> out;
    if (path.empty()) return out;
    std::ifstream in(path);
    if (!in) {
      std::cerr << "Warning: could not open dataset file: " << path << "\n";
      return out;
    }
    std::string line;
    while (std::getline(in, line)) {
      // Keep raw line including empty string if present as acceptable member
      // Strip trailing CR (Windows line endings) if present
      if (!line.empty() && line.back() == '\r') line.pop_back();
      out.insert(line);
    }
    return out;
  }
};

} // namespace lstar
