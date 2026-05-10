# FLUX Agentic Compiler & Runtime — Architecture

## What Is This?

A self-discovering, self-optimizing constraint engine that:

1. **Probes** the system at startup (compilers, libraries, hardware, SIMD)
2. **Compiles** kernels in every available language (C, Zig, Fortran, Nim)
3. **Benchmarks** every implementation, picks the winner
4. **Hot-swaps** when it finds a faster path
5. **Remembers** what worked across sessions

Like Zig's `@cImport` but agentic — it doesn't just translate headers,
it discovers what's installed, benchmarks alternatives, and self-optimizes.

## Architecture

```
┌─────────────────────────────────────────────────┐
│               FLUX Runtime                       │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │ System   │  │ Foreign  │  │ C Header     │  │
│  │ Prober   │  │ Bridge   │  │ Translator   │  │
│  └────┬─────┘  └────┬─────┘  └──────┬───────┘  │
│       │              │               │           │
│  ┌────▼──────────────▼───────────────▼───────┐  │
│  │          Primitive Engine                  │  │
│  │                                            │  │
│  │  norm ──── [python, C, Zig, Nim, numpy]   │  │
│  │  check ── [python, C AVX2, numpy]         │  │
│  │  bloom ── [C AVX2, Zig, Fortran, numpy]   │  │
│  │  snap ─── [python, C batch]               │  │
│  │  fold ─── [python, C, Fortran, numpy]     │  │
│  │                                            │  │
│  │  → Benchmark all → Pick winner → Execute  │  │
│  └───────────────┬────────────────────────────┘  │
│                  │                                │
│  ┌───────────────▼────────────────────────────┐  │
│  │        Performance Database                │  │
│  │  (persists across sessions)                │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  API:                                            │
│  rt.import_c("header.h")  # like @cImport       │
│  rt.load_library("fftw3") # like dlopen         │
│  rt.eval_r("code")        # call R              │
│  rt.eval_matlab("code")   # call MATLAB         │
│  rt.jit_compile(src, lang="zig") # JIT any lang │
│  rt.call("norm", a, b)    # auto-routed         │
└─────────────────────────────────────────────────┘
```

## Key Findings from Real Hardware (Ryzen AI 9 HX 370)

### FFI Overhead Dominates for Small Primitives

| Primitive | Python scalar | C (via ctypes) | Overhead |
|-----------|-------------|-----------------|----------|
| norm      | 84ns        | 256ns           | 3x slower! |
| check     | 483ns       | 5488ns          | 11x slower! |
| bloom     | 920ns (np)  | 4053ns          | 4.4x slower! |

**Why?** ctypes marshaling costs ~200-500ns per call. For a 1-cycle computation
like Eisenstein norm, the FFI setup costs 1000x more than the computation.

**Implication:** The agent correctly learns to use Python for small primitives
and only routes to C/numpy for batch operations where the FFI cost amortizes.

### Fortran Wins for Array Operations

| Primitive | Fortran | C | numpy |
|-----------|---------|---|-------|
| fold (n=8)| 2442ns  | 2672ns | 4417ns |

Fortran's `IOR` is a language keyword. The compiler emits SIMD directly.
No library calls, no marshaling — just register operations.

### Winner Summary

| Primitive | Winner | Latency | Why |
|-----------|--------|---------|-----|
| norm | Python | 84ns | FFI overhead > computation |
| check | Python | 483ns | FFI overhead > computation |
| bloom | numpy | 921ns | Already C underneath, no extra marshaling |
| fold | Fortran | 2442ns | Whole-array IOR, compiler emits SIMD |
| snap | Python | 353ns | FFI overhead > computation |
| norm_batch | C AVX2 | 6016ns | FFI amortized over n elements |
| snap_batch | C AVX2 | 12262ns | FFI amortized over n elements |

## C Header Import (@cImport equivalent)

```python
# Like Zig's @cImport:
rt = FluxRuntime()

# Import any C header — compile to .so, bind via ctypes
lib = rt.import_c("eisenstein.h", include_dirs=["/path/to/headers"])
result = lib.eisenstein_norm(3, 7)

# Load any system library
fftw = rt.load_library("fftw3")
# ... bind functions manually or via header parsing
```

## Foreign Language Bridge

```python
# R — proven correct in polyformalism tests
result = rt.eval_r("bitor(c(1,2,3), c(2,3,4))")

# MATLAB — for analysis workloads
result = rt.eval_matlab("norm([3,0] - [0,1])")

# Julia — for scientific computing
result = rt.eval_julia("sqrt(3)/2")

# JIT compile in any language
lib = rt.jit_compile(source_code, name="my_kernel", lang="zig")
lib.my_function()
```

## Self-Optimization Lifecycle

1. **First run:** Discover everything, compile kernels, benchmark, save winners
2. **Subsequent runs:** Load perf DB, check if hardware changed, re-benchmark if needed
3. **During execution:** Profile calls, detect hot paths
4. **Hot-swap:** When a primitive exceeds threshold, JIT-compile specialized version
5. **Shutdown:** Persist new performance data

The runtime gets faster across sessions because it remembers what worked
on this specific hardware with this specific software stack.

## Files

- `flux_runtime_v2.py` — Main runtime (40KB, self-contained)
- `fluxc_autotune.py` — Agentic autodiscovery compiler (benchmarks C strategies)
- `flux_runtime.py` — v1 runtime with Zig/Fortran auto-binding
- `flux_refactor.py` — JIT refactoring engine for hot paths
- `perf_db.json` — Persisted performance database
- `autotune_results/` — Saved benchmark results from autotune

## What Makes This Different

1. **No hardcoded strategies** — the agent discovers what's fastest by measuring
2. **C header import** — like Zig but at runtime, not compile time
3. **Multi-language** — C, Zig, Fortran, Nim compiled and benchmarked at startup
4. **Foreign bridge** — R, MATLAB, Julia callable from the same runtime
5. **Persistent learning** — remembers what worked across sessions
6. **FFI-aware** — correctly avoids ctypes for small operations where overhead dominates
7. **Self-refactoring** — detects hot paths and JIT-compiles optimized replacements
