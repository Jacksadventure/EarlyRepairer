#include <re2/re2.h>
#include <algorithm>
#include <cctype>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <regex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

namespace fs = std::filesystem;

static std::string trim(const std::string &s) {
    auto begin = s.begin();
    auto end = s.end();

    while (begin != end && std::isspace(static_cast<unsigned char>(*begin))) {
        ++begin;
    }
    if (begin == end) return std::string();

    do {
        --end;
    } while (end != begin && std::isspace(static_cast<unsigned char>(*end)));
    return std::string(begin, end + 1);
}

static bool read_file_trim(const fs::path& p, std::string& out) {
    std::ifstream in(p, std::ios::in | std::ios::binary);
    if (!in) return false;
    std::ostringstream oss;
    oss << in.rdbuf();
    out = trim(oss.str());
    return true;
}

int main(int argc, char** argv) {
    if (argc < 3 || argc > 4) {
        std::cerr << "Usage: " << (argc > 0 ? argv[0] : "bench_re2_single")
                  << " <Category: Date|Time|URL|ISBN|IPv4|IPv6|FilePath> <input_dir> [iterations=10]\n";
        return 2;
    }
    const std::string category = argv[1];
    const fs::path input_dir = argv[2];
    const int iterations = (argc == 4 ? std::max(1, std::stoi(argv[3])) : 10);

    const std::unordered_map<std::string, std::string> patterns = {
        {"Date", R"(^\d{4}-\d{2}-\d{2}$)"},
        {"Time", R"(^\d{2}:\d{2}:\d{2}$)"},
        {"URL",  R"(^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$)"},
        {"ISBN", R"(^(?:\d[- ]?){9}[\dX]$)"},
        {"IPv4", R"(^(\d{1,3}\.){3}\d{1,3}$)"},
        {"IPv6", R"(^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$)"},
        {"FilePath", R"(^[a-zA-Z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]*$)"}
    };

    auto it = patterns.find(category);
    if (it == patterns.end()) {
        std::cerr << "Unknown category: " << category << "\n";
        return 2;
    }
    const std::string& pattern = it->second;

    if (!fs::exists(input_dir) || !fs::is_directory(input_dir)) {
        std::cerr << "Input directory not found or not a directory: " << input_dir << "\n";
        return 2;
    }

    // Collect files
    std::vector<fs::path> files;
    for (auto const& entry : fs::directory_iterator(input_dir)) {
        if (entry.is_regular_file()) {
            files.push_back(entry.path());
        }
    }
    std::sort(files.begin(), files.end());
    if (files.empty()) {
        std::cerr << "No files found in directory: " << input_dir << "\n";
        return 2;
    }

    // Compile regex once
    RE2 re(pattern);
    if (!re.ok()) {
        std::cerr << "Error: Invalid RE2 pattern for category '" << category << "'.\n";
        return 1;
    }

    const size_t file_count = files.size();
    const size_t total_checks = static_cast<size_t>(iterations) * file_count;

    // Warm-up: read first file and run a couple of matches to mitigate cold-start effects
    {
        std::string warm_data;
        if (read_file_trim(files.front(), warm_data)) {
            volatile bool sink = false;
            sink ^= RE2::FullMatch(warm_data, re);
            sink ^= RE2::FullMatch(warm_data, re);
        }
    }

    size_t matches = 0, reads_ok = 0;
    auto t0 = std::chrono::steady_clock::now();

    for (int itn = 0; itn < iterations; ++itn) {
        for (const auto& p : files) {
            std::string data;
            if (!read_file_trim(p, data)) continue;
            ++reads_ok;
            if (RE2::FullMatch(data, re)) {
                ++matches;
            }
        }
    }

    auto t1 = std::chrono::steady_clock::now();
    auto elapsed_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count();
    double elapsed_ms = elapsed_ns / 1e6;
    double per_check_us = (total_checks ? (elapsed_ns / 1000.0) / static_cast<double>(total_checks) : 0.0);
    double throughput = (elapsed_ns > 0 ? (static_cast<double>(total_checks) * 1e9) / static_cast<double>(elapsed_ns) : 0.0);

    // JSON output for easy parsing
    std::cout << "{"
              << "\"mode\":\"single-process\","
              << "\"category\":\"" << category << "\","
              << "\"files\":" << file_count << ","
              << "\"iterations\":" << iterations << ","
              << "\"checks\":" << total_checks << ","
              << "\"reads_ok\":" << reads_ok << ","
              << "\"matches\":" << matches << ","
              << "\"elapsed_ms\":" << elapsed_ms << ","
              << "\"per_check_us\":" << per_check_us << ","
              << "\"throughput_checks_per_sec\":" << throughput
              << "}" << std::endl;

    return 0;
}
