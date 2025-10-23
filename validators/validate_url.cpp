#include <re2/re2.h>
#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <algorithm>
#include <cctype>

static std::string trim(const std::string &s) {
    auto begin = s.begin();
    auto end = s.end();
    while (begin != end && std::isspace(static_cast<unsigned char>(*begin))) ++begin;
    if (begin == end) return std::string();
    do { --end; } while (end != begin && std::isspace(static_cast<unsigned char>(*end)));
    return std::string(begin, end + 1);
}

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "Usage: " << (argc > 0 ? argv[0] : "validate_url") << " <file_path>\n";
        return 2;
    }
    const char* file_path = argv[1];

    // URL full match (anchored)
    const std::string pattern = R"(^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$)";

    std::ifstream in(file_path);
    if (!in) {
        std::cerr << "Error: File '" << file_path << "' not found.\n";
        return 1;
    }
    std::ostringstream oss; oss << in.rdbuf();
    std::string data = trim(oss.str());

    RE2 re(pattern);
    if (!re.ok()) {
        std::cerr << "Error: Invalid RE2 pattern for URL.\n";
        return 1;
    }

    bool ok = RE2::FullMatch(data, re);
    return ok ? 0 : 1;
}
