# L* (Angluin) in C++ for EarlyRepairer

This module is a clean C++17 implementation of the core L* algorithm to learn a DFA from membership and equivalence queries, adapted to this repo’s data layout. It ports the structure and behavior of the Python L* implementation (`lstar.py` and `lstar-standalone/lstar/observation_table.py`) into a small, embeddable library with a CLI.

Features
- Observation table with closedness/consistency maintenance
- DFA construction from table row signatures
- Dataset-backed Oracle:
  - membership: positives=1, negatives=0, unknown configurable (default: 0)
  - equivalence: checks DFA against all labeled samples; returns first counterexample
- CLI to learn from positive/negative datasets and export Graphviz DOT

Directory layout
- include/lstar/
  - DFA.hpp               Minimal DFA representation (accept/transition/DOT export)
  - ObservationTable.hpp  Observation table + L* table ops + DFA build
  - LStar.hpp             Orchestration of the L* loop
  - DatasetOracle.hpp     Oracle backed by datasets on disk or in-memory
- src/                    TU stubs (headers are header-only)
- apps/lstar_cli.cpp      CLI tool

Build
Requirements: CMake ≥ 3.16, a C++17 compiler (AppleClang/Clang/GCC), optional Graphviz (for PNG export).

- From repo root:
  mkdir -p lstar_cpp/build
  cd lstar_cpp/build
  cmake ..
  cmake --build . -j

This builds:
- liblstar.a (header-only symbols; archive may have empty TOC warning)
- lstar_cli (the CLI tool)

Usage (CLI)
By default it targets this repo’s data files.

- Learn from datasets and write DOT:
  cd lstar_cpp/build
  ./lstar_cli -p ../../positive/positives.txt -n ../../negative/negatives.txt -o ../../learned.dot

- Options:
  - -p <file>   positives dataset (default: positive/positives.txt)
  - -n <file>   negatives dataset (default: negative/negatives.txt)
  - -A <chars>  alphabet override as a string (inferred from datasets by default)
  - -o <file>   output DOT path (default: stdout)
  - --default-negative[=0|1]  treat unknown samples as negative (default: 1)

- Visualize DOT (requires Graphviz):
  dot -Tpng ../../learned.dot -o ../../learned.png
  open ../../learned.png

Embedding (Library API)
Example: learn from datasets in-process and export DOT.

#include "lstar/DatasetOracle.hpp"
#include "lstar/LStar.hpp"
#include "lstar/ObservationTable.hpp"

int main() {
  // Load datasets
  auto oracle = lstar::DatasetOracle::from_files("positive/positives.txt", "negative/negatives.txt");
  // Build alphabet (or supply a custom vector<char>)
  auto alpha = lstar::DatasetOracle::infer_alphabet(oracle.positives(), oracle.negatives());
  lstar::ObservationTable table(alpha);

  // Run L*
  lstar::DFA dfa = lstar::LStarLearner::learn(table, oracle);

  // Export DOT
  std::string dot = dfa.to_dot(table.A());
  // ... write to file or post-process
}

Notes and limitations
- This implementation uses a dataset-based equivalence oracle. It declares success when the learned DFA classifies all provided labeled samples correctly. For true PAC-style equivalence and regex-backed membership (as in the Python tutorial’s Teacher), add an alternative Oracle implementation.
- The static library contains header-only code; the warning about empty table of contents is benign.
- If your datasets contain characters outside simple ASCII, ensure your terminal/file encoding matches your expected alphabet; you can override with -A.

Roadmap (optional)
- Add Regex/PAC oracle mirroring the Python Teacher (PAC bounds, cooperative counterexamples).
- Add DOT minimization or export minimized DFA.
- Add JSON export/import of DFA and table snapshots.

中文简要说明
- 该模块用 C++17 重写了 L* 算法（与 lstar.py 行为一致），支持：
  - 基于观测表的闭合性/一致性维护
  - 由观测表构造 DFA
  - 基于数据集的 Oracle（正/负样本；未知可配置为负/正）
  - 命令行工具从数据集学习并导出 DOT
- 构建与运行同上；使用 Graphviz 可将 DOT 转为 PNG。
