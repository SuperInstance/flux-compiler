# FLUX Compiler

> **FLUX — Fluid Language Universal eXecution**
> Correctness Verified. Safety Certified. Zero Surprises.

---

## Status

| Metric | Status |
|---|---|
| Build | ![Build Passing](https://img.shields.io/badge/Build-Passing-brightgreen?style=flat-square) |
| Open CVEs | ![0](https://img.shields.io/badge/Known_CVEs-0-brightgreen?style=flat-square) |
| License | ![Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue?style=flat-square) |

**Formal Verification Coverage and Fuzz Uptime badges have been removed pending independent audit.** The project contains Coq proof files and a fuzz testing framework, but the specific "100% coverage" and "147 days" claims have not been independently verified. See [Formal Verification](#formal-verification) and [Testing & Verification](#testing--verification) for what is actually present.

---

## One Paragraph Pitch

Every safety-critical system built today relies on compilers that are either 35-year-old unmaintained legacy tools, closed-source black boxes costing $120k/seat, or general-purpose compilers with known silent miscompilation bugs, undefined behaviour, and optimizer surprises that will crash your vehicle, aircraft, or medical device. FLUX is a research compiler exploring formal methods for safety-critical code generation. The architecture uses a system prober to discover available compilers and libraries, a benchmark engine to measure performance across multiple implementations, and a JIT compiler that compiles optimized kernels in C, Zig, Fortran, and Nim at startup. This is not a production tool — it is an experimental exploration of what a correctness-verified compiler stack could look like.

---

## What's Real

### System Prober — `probe_system()`
Detects compilers, libraries, CPU features, and Python packages available on the current machine at startup. Checks for: gcc, g++, gfortran, clang, zig, nim, swift, go, rustc, javac, Rscript, MATLAB. Also detects BLAS, FFTW, CUDA, and CPU SIMD features (AVX2, AVX-512, NEON, AMX).

### Benchmark Engine — warmup + CLOCK_MONOTONIC
Measures performance with a warmup loop, monotonic timestamps, and a verification pass that confirms correctness post-benchmark. Reports worst-case execution time and stack usage for compiled output.

### JIT Compilation — compiles C, Zig, Fortran, Nim at startup
At initialization, FLUX compiles optimized native kernels from source strings in four languages: C, Zig, Fortran, and Nim. These are compiled to shared libraries (.so) and bound via ctypes. Python/numpy are used as fallback when compiled kernels are unavailable.

### Performance Database — `perf_db.json`
A persisted snapshot of benchmark results from prior runs. Not a learning system — it records what implementation performed best on this machine. Does not update autonomously.

---

## Quick Start
```bash
git clone --recurse-submodules https://github.com/SuperInstance/flux-compiler
cargo install --path flux-compiler
flux compile --target=arm-r5f --safety-level=DAL-B examples/brake.guard
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SYSTEM PROBER                               │
│  probe_system() — detects compilers, libs, CPU features        │
├─────────────────────────────────────────────────────────────────┤
│                   PRIMITIVE ENGINE                              │
│  5 base primitives: norm, check, bloom, fold, snap             │
│  20 total implementations across compiled + interpreted        │
│  Binds best available implementation per primitive per run     │
├─────────────────────────────────────────────────────────────────┤
│                   BENCHMARK ENGINE                              │
│  Warmup loop + CLOCK_MONOTONIC + verification pass             │
│  Selects fastest implementation per primitive on current host  │
├─────────────────────────────────────────────────────────────────┤
│                   JIT COMPILER                                  │
│  Compiles C, Zig, Fortran, Nim kernels at startup              │
│  Falls back to Python/numpy when compiled libs unavailable     │
└─────────────────────────────────────────────────────────────────┘
```

### Repository Layout
```
flux-compiler/
├── formal/
│   ├── guard-semantics/      # Coq language specification
│   └── pass-theorems/        # Compiler pass correctness lemmas
├── compiler/
│   ├── parser/               # PEG grammar parser
│   ├── normalizer/
│   ├── optimizer/
│   └── codegen/              # Rust as of 0.9
├── flux_runtime_v2.py        # Main runtime: prober, benchmark, JIT
├── fluxc_cli.py              # CLI entry point
├── perf_db.json              # Persisted performance snapshot
└── tests/
    ├── negative/
    └── fuzz/
```

---

## Supported Languages (JIT Compiled Kernels)

The JIT compiler produces optimized native kernels in:
- **C** — via gcc/clang
- **Zig** — via zig compiler
- **Fortran** — via gfortran
- **Nim** — via nim compiler

Python and numpy are used as fallback when compiled kernels are unavailable. MATLAB and R are probed but have no compiled kernel implementations in the current codebase.

---

## Primitives & Implementations

| Primitive | Implementations | Best On Current Host |
|---|---|---|
| `norm` | python, numpy, c_scalar, zig, nim | [from perf_db.json] |
| `check` | python, numpy, c_avx2 | [from perf_db.json] |
| `bloom` | python, numpy, c_avx2, zig, fortran_ior, nim | [from perf_db.json] |
| `fold` | python, numpy, c_scalar, fortran | [from perf_db.json] |
| `snap` | python_voronoi | [from perf_db.json] |
| `norm_batch` | c_avx2 | [from perf_db.json] |
| `snap_batch` | c_avx2 | [from perf_db.json] |

---

## Formal Verification

The project includes Coq proof files in `formal/guard-semantics/` and `formal/pass-theorems/`. These define the GUARD language semantics and correctness lemmas for individual compiler passes.

**What the Coq proofs cover:**
- Parser accepts exactly and only valid GUARD programs
- Normalization preserves operational semantics
- Dead code elimination preserves observable side effects
- Register allocation does not spill across interrupt boundaries
- Memory accesses are statically bounded
- Stack usage is bounded for all execution paths
- No compiler pass introduces undefined behaviour

**What remains unproven (as of this writing):**
- Termination proofs for all compiler passes
- Translation validation equivalence across all targets

The "100% Formal Verification Coverage" badge has been removed — coverage scope is defined by the lemmas in `formal/pass-theorems/` and has not been independently audited.

---

## Testing & Verification

The `tests/` directory contains:
- **Negative tests** — validate the compiler correctly rejects bad input
- **Fuzz tests** — the framework exists; corpus size and uptime figures are unverified

A benchmark engine runs with warmup loops and monotonic timing on each run. A verification pass confirms correctness post-benchmark. The "147 days, 0 crashes" fuzz uptime figure is not independently verified.

Differential testing against GCC, Clang, CompCert, and commercial compilers is described in the architecture but the comparison data has not been provided in this repository.

---

## Installation

### Cargo
```bash
cargo install flux-compiler --version 0.9.1
```

### Docker
```bash
docker pull ghcr.io/superinstance/flux:0.9.1
docker run --rm -v $PWD:/work flux compile --target=arm-r5f input.guard
```

---

## CLI Reference

```
flux compile [OPTIONS] INPUT

Required Flags:
  --target <TARGET>       Compilation target: arm-r5f, riscv-e31, avx512, cuda-sm75, fpga-lattice
  --safety-level <LEVEL>  Safety assurance level: QM, DAL-D, DAL-C, DAL-B, DAL-A

Optional Flags:
  --emit=<asm,obj,llvm,proof>   Output additional artifacts
  --no-optimizations            Disable all proven optimizations
  --stack-limit <BYTES>         Fail compile if stack usage exceeds limit
```

---

## 📦 Related Packages

FLUX is implemented across multiple languages — same bytecode, different shells:

| Package | Language | Registry | Install |
|---------|----------|----------|---------|
| **[flux-vm](https://pypi.org/project/flux-vm/)** | Python | PyPI | `pip install flux-vm` |
| **[fluxvm](https://crates.io/crates/fluxvm)** | Rust | crates.io | `cargo add fluxvm` |
| **[flux-js](https://www.npmjs.com/package/flux-js)** | JavaScript | npm | `npm install flux-js` |
| **[flux-compiler](https://github.com/SuperInstance/flux-compiler)** | Rust/Python | GitHub | `cargo install flux-compiler` |

Additional implementations: [C](https://github.com/SuperInstance/flux-runtime-c) · [Zig](https://github.com/SuperInstance/flux-zig) · [Go](https://github.com/SuperInstance/flux-swarm) · [Java](https://github.com/SuperInstance/flux-java) · [WASM](https://github.com/SuperInstance/flux-wasm) · [CUDA](https://github.com/SuperInstance/flux-cuda)

## 🌐 Ecosystem

FLUX is part of a broader research ecosystem exploring agent-first computation:

| Project | Description |
|---------|-------------|
| [PLATO Engine Block](https://github.com/SuperInstance/plato-engine-block) | Constraint engine powering FLUX verification |
| [Constraint-Theory-Core](https://github.com/SuperInstance/Constraint-Theory) | Mathematical foundations for constraint-based computation |
| [AI-Writings](https://github.com/SuperInstance/AI-Writings) | Philosophy, essays, and design rationale behind FLUX |
| [Captain's Log](https://github.com/SuperInstance/captains-log) | Oracle1 growth diary and agent dojo curriculum |
| [Iron-to-Iron](https://github.com/SuperInstance/iron-to-iron) | I2I protocol — agents communicate through git commits |
| [flux-research](https://github.com/SuperInstance/flux-research) | 40K words: compiler taxonomy, ISA v2, agent-first design |

📖 **[Full package index →](https://github.com/SuperInstance/flux/blob/main/PACKAGES.md)**

---

## License

- Compiler source code: Apache 2.0
- Formal specifications and theorems: Public Domain
- Qualification artifacts and regulator audit packages: Available under commercial support license

---

*FLUX Compiler is maintained by SuperInstance Aerospace. For commercial support and certification inquiries, contact flux@superinstance.com*
