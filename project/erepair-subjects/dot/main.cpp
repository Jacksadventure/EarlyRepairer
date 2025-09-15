// main.cpp  – DOT validator (0 / 1 / 255, ordinary errors beat EOF)
// -----------------------------------------------------------------
//
//   0   valid DOT
//   1   any lexer error (except EOF-in-string) OR any parser error with non‑EOF token
//   255 lexer reached EOF inside token  OR  parser offending token == EOF
//   2   usage / I‑O error
//
#include <fstream>
#include <iostream>
#include <memory>

#include "antlr4-runtime.h"
#include "DOTLexer.h"
#include "DOTParser.h"

// ---------------------------------------------------------------
// Listener: sets flags for three categories
// ---------------------------------------------------------------
class ErrorFlags : public antlr4::BaseErrorListener {
public:
    bool lexerOrdinary  = false;   // illegal char, bad escape, etc.
    bool lexerAtEOF     = false;   // unterminated string/comment
    bool parserOrdinary = false;   // offending token != EOF
    bool parserAtEOF    = false;   // offending token == EOF

    void syntaxError(antlr4::Recognizer* recognizer,
                     antlr4::Token* offendingSymbol,
                     size_t, size_t,
                     const std::string&,
                     std::exception_ptr) override
    {
        if (offendingSymbol == nullptr) {               // lexer error
            auto *lex = dynamic_cast<antlr4::Lexer*>(recognizer);
            if (lex && lex->getInputStream()->LA(1) == antlr4::Token::EOF)
                lexerAtEOF = true;                      // hit EOF in token
            else
                lexerOrdinary = true;
        } else {                                        // parser error
            if (offendingSymbol->getType() == antlr4::Token::EOF)
                parserAtEOF = true;
            else
                parserOrdinary = true;
        }
        // keep parsing so we can see every kind of error
    }
};

#include <fcntl.h>
#include <unistd.h>

int main(int argc, const char* argv[]) {
    /* ---------- 0. file open ------------------------------------- */
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <file.dot>\n";
        return 2;
    }

    std::unique_ptr<std::istream> in_ptr;
    std::ifstream file_in;
    FILE* fd_file = nullptr;
    int fd = -1;
    bool using_fd = false;

    if (strncmp(argv[1], "/dev/fd/", 8) == 0) {
        fd = atoi(argv[1] + 8);
        if (fd > 0) {
            fd_file = fdopen(fd, "r");
            if (fd_file) {
                in_ptr.reset(new std::istream(fd_file->rdbuf()));
                using_fd = true;
            }
        }
    }
    if (!using_fd) {
        file_in.open(argv[1], std::ios::in | std::ios::binary);
        if (!file_in) { perror("open"); return 2; }
        in_ptr.reset(&file_in, [](...){}); // no-op deleter for stack object
    }

    /* ---------- 1. ANTLR setup ----------------------------------- */
    antlr4::ANTLRInputStream  input(*in_ptr);
    DOTLexer                  lexer(&input);
    antlr4::CommonTokenStream tokens(&lexer);
    DOTParser                 parser(&tokens);

    lexer.removeErrorListeners();
    parser.removeErrorListeners();

    auto flags = std::make_shared<ErrorFlags>();
    lexer.addErrorListener(flags.get());
    parser.addErrorListener(flags.get());

    parser.graph();                                 // parse whole file

    /* ---------- 2. precedence logic ------------------------------ */
    if (flags->lexerOrdinary || flags->parserOrdinary)   return 1;
    if (flags->lexerAtEOF    || flags->parserAtEOF)      return 255;
    return 0;
}
