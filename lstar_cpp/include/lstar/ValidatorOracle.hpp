#pragma once

#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <unordered_set>
#include <utility>
#include <vector>

#include "lstar/DFA.hpp"
#include "lstar/ObservationTable.hpp"

namespace lstar {

// Helper: derive alphabet from datasets (fallback to 'ab' if empty)
inline std::vector<char> derive_alphabet_from_examples(const std::unordered_set<std::string>& positives,
                                                       const std::unordered_set<std::string>& negatives) {
  std::set<char> alpha;
  for (const auto& s : positives) for (unsigned char c : s) alpha.insert(static_cast<char>(c));
  for (const auto& s : negatives) for (unsigned char c : s) alpha.insert(static_cast<char>(c));
  if (alpha.empty()) return std::vector<char>{'a','b'};
  return std::vector<char>(alpha.begin(), alpha.end());
}

// Validator-backed Oracle:
// - is_member: uses external validators (validators/regex/* or validators/*) or falls back to "python3 match.py <Category> <file>"
// - is_equivalent: checks all positives must be accepted by DFA; negatives must be rejected (optional toggles can be added later)
class ValidatorOracle : public Oracle {
public:
  ValidatorOracle(std::string category,
                  std::unordered_set<std::string> positives,
                  std::unordered_set<std::string> negatives,
                  std::vector<std::string> validator_override_cmd = {},
                  bool check_negatives = true)
      : category_(std::move(category)),
        positives_(std::move(positives)),
        negatives_(std::move(negatives)),
        validator_cmd_override_(std::move(validator_override_cmd)),
        check_negatives_(check_negatives) {}

  int is_member(const std::string& q) override {
    std::cout << "[debug] ValidatorOracle: is_member query: \"" << q << "\"\n";
    // Memoize
    auto it = mem_cache_.find(q);
    if (it != mem_cache_.end()) return it->second ? 1 : 0;

    bool ok = validate_with_match(category_, q, validator_cmd_override_);
    mem_cache_.emplace(q, ok);
    std::cout << "[debug] ValidatorOracle: is_member result: " << (ok ? "accepted" : "rejected") << "\n";
    return ok ? 1 : 0;
  }

  std::pair<bool, std::string> is_equivalent(const DFA& dfa,
                                             const std::vector<char>& /*alphabet*/) override {
    // Positives must be accepted by hypothesis DFA
    for (const auto& p : positives_) {
      if (!dfa.accepts(p)) return {false, p};
    }
    // Negatives must be rejected (optional)
    if (check_negatives_) {
      for (const auto& n : negatives_) {
        if (dfa.accepts(n)) return {false, n};
      }
    }
    return {true, ""};
  }

  const std::unordered_set<std::string>& positives() const { return positives_; }
  const std::unordered_set<std::string>& negatives() const { return negatives_; }

private:
  std::string category_;
  std::unordered_set<std::string> positives_;
  std::unordered_set<std::string> negatives_;
  std::vector<std::string> validator_cmd_override_;
  bool check_negatives_{true};
  std::unordered_map<std::string, bool> mem_cache_;

  static std::string map_category_to_base(const std::string& category) {
    if (category == "Date") return "date";
    if (category == "Time") return "time";
    if (category == "URL") return "url";
    if (category == "ISBN") return "isbn";
    if (category == "IPv4") return "ipv4";
    if (category == "IPv6") return "ipv6";
    if (category == "FilePath") return "pathfile";
    // default: lowercase
    std::string out = category;
    for (auto& c : out) c = static_cast<char>(::tolower(static_cast<unsigned char>(c)));
    return out;
  }

  static bool file_exists(const std::string& p) {
    struct stat st{};
    return ::stat(p.c_str(), &st) == 0 && S_ISREG(st.st_mode);
  }

  static std::string shell_escape(const std::string& s) {
    // Basic escaping for POSIX shell: wrap in single quotes and escape internal '
    std::string out;
    out.reserve(s.size() + 2);
    out.push_back('\'');
    for (char c : s) {
      if (c == '\'') { out += "'\\''"; }
      else out.push_back(c);
    }
    out.push_back('\'');
    return out;
  }

  static std::string write_temp_file(const std::string& content) {
    // Create a secure temp file using mkstemp
    std::string tmpl = "/tmp/lstar_oracle_XXXXXX";
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');
    int fd = ::mkstemp(buf.data());
    if (fd == -1) return std::string(); // failure
    std::string path(buf.data());
    // write content
    ssize_t w = ::write(fd, content.data(), content.size());
    (void)w;
    ::close(fd);
    return path;
  }

  static void remove_file_quiet(const std::string& path) {
    if (!path.empty()) ::unlink(path.c_str());
  }

  static bool run_system_cmd_success(const std::string& cmdline) {
    // Use system(); returns exit status. On POSIX, WEXITSTATUS(status)==0 indicates success.
    int rc = ::system(cmdline.c_str());
    if (rc == -1) return false;
    if (WIFEXITED(rc)) return WEXITSTATUS(rc) == 0;
    return false;
  }

  static bool validate_with_match(const std::string& category,
                                  const std::string& text,
                                  const std::vector<std::string>& validator_override_cmd) {
    // Write text to temp file
    std::string tmp = write_temp_file(text);
    if (tmp.empty()) return false;

    // Build command
    std::ostringstream cmd;
    if (!validator_override_cmd.empty()) {
      // Custom command + file
      for (size_t i = 0; i < validator_override_cmd.size(); ++i) {
        if (i) cmd << " ";
        cmd << validator_override_cmd[i];
      }
      cmd << " " << shell_escape(tmp);
    } else {
      // Robust path resolution relative to current working dir (works from lstar_cpp/build/)
      std::string base = map_category_to_base(category);
      std::vector<std::string> prefixes = {"", "../", "../../", "../../../"};
      std::string chosen;

      // Try native validators first
      for (const auto& pref : prefixes) {
        std::string p = pref + "validators/regex/validate_" + base;
        if (file_exists(p)) { chosen = p; break; }
      }
      if (chosen.empty()) {
        for (const auto& pref : prefixes) {
          std::string p = pref + "validators/validate_" + base;
          if (file_exists(p)) { chosen = p; break; }
        }
      }
      if (!chosen.empty()) {
        cmd << shell_escape(chosen) << " " << shell_escape(tmp);
      } else {
        // Fallback to python3 match.py <Category> <file>
        std::string match_path = "match.py";
        bool found = false;
        for (const auto& pref : prefixes) {
          std::string cand = pref + "match.py";
          if (file_exists(cand)) { match_path = cand; found = true; break; }
        }
        (void)found; // informational only
        cmd << "python3 " << shell_escape(match_path) << " " << shell_escape(category) << " " << shell_escape(tmp);
      }
    }

    bool ok = run_system_cmd_success(cmd.str());
    remove_file_quiet(tmp);
    return ok;
  }
};

} // namespace lstar
