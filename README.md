# FLUX Constraint-to-Native Compiler

**GUARD → LLVM IR → AVX-512 / Wasm / eBPF / RISC-V / Fortran**

FLUX is a constraint-to-native compiler that takes declarative constraint files written in GUARD DSL and compiles them through LLVM IR to five native targets. Seven proven theorems guarantee correctness from source to machine code.

## Architecture

```
GUARD (.guard)
    ↓ guardc (verified Rust compiler)
    ↓ guard2mask (GUARD→FLUX mask compiler)
    ↓ fluxc (Python → LLVM backend)
LLVM IR
    ↓ 5 codegen targets
    ├── AVX-512     (22.3B checks/s)
    ├── Wasm        (browser-grade)
    ├── eBPF        (kernel-level)
    ├── RISC-V      (embedded)
    └── Fortran     (HPC legacy)
```

## Components

| Directory | Description |
|-----------|-------------|
| `compiler/` | `fluxc.py` (FLUX compiler CLI) + `flux_llvm_backend.py` (LLVM IR codegen) |
| `guard2mask/` | Rust crate: GUARD parser + GUARD→FLUX mask compiler |
| `guardc/` | Verified Rust compiler: GUARD AST → LCIR → CIR → proof verification → codegen |
| `guard-dsl/` | GUARD DSL specification, grammar (EBNF), error catalog, examples |
| `.github/workflows/` | CI: metal-bake (compile, test, bench, proof-check) |

## Usage

```bash
# Compile a GUARD file to target
fluxc compile <file.guard> --target avx512

# Benchmark compiled output
fluxc bench <file.guard> --target avx512

# Show generated LLVM IR
fluxc show <file.guard>
```

## Benchmarks

| Target | Throughput | Notes |
|--------|-----------|-------|
| AVX-512 | 22.3B checks/s | Vectorized constraint evaluation |
| Compiled batch | 5.36B checks/s | Full pipeline including dispatch |

## Proven Correctness

Seven theorems proven in the `guardc` proof system guarantee:
1. **Soundness** — No invalid state accepted
2. **Completeness** — All valid states accepted
3. **Compilation correctness** — Target code preserves source semantics
4. **Mask equivalence** — FLUX masks match GUARD constraints
5. **Lowering fidelity** — Each IR lowering step preserves meaning
6. **Codegen verification** — Generated code matches proven IR
7. **End-to-end guarantee** — Source constraint → native code is correct

## License

Apache-2.0
