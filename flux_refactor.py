#!/usr/bin/env python3
"""
FLUX Refactoring Engine — JIT-compiles optimized replacements for hot paths.

When the runtime detects a primitive is called >1000 times with average latency
above a threshold, it:
1. Generates a C implementation optimized for the exact workload pattern
2. Compiles it with gcc -O2 -mavx2
3. Loads the shared library
4. Hot-swaps the binding

This is the "agentic" part — the runtime rewrites itself at runtime.
"""
import subprocess
import tempfile
import os
import ctypes
import time
import json
from pathlib import Path
from typing import Optional, Dict, Callable, Any

PERF_DB = Path("/tmp/flux-compiler/perf_db.json")
REFACTOR_DIR = Path("/tmp/flux-compiler/refactored")
REFACTOR_DIR.mkdir(exist_ok=True)

# ============================================================
# C KERNEL TEMPLATES — parameterized for exact workload
# ============================================================

CONSTRAINT_CHECK_C = """
#include <stdint.h>
#include <string.h>
#include <immintrin.h>

// Auto-refactored constraint check for n={N} elements
// Strategy: {strategy}
// Hot path: called {calls} times, avg {avg_ns:.0f}ns in Python

int constraint_check_{N}(const int32_t *lower, const int32_t *upper, const int32_t *values) {{
{body}
}}

// Batch: check multiple constraint sets at once
int constraint_check_batch_{N}(const int32_t *lower, const int32_t *upper,
                                const int32_t *values, int batch_size, int stride) {{
    for (int b = 0; b < batch_size; b++) {{
        const int32_t *v = values + b * stride;
        {batch_body}
    }}
    return 1;  // all passed
}}
"""

AVX2_CHECK_BODY = """
    for (int i = 0; i + 8 <= {N}; i += 8) {
        __m256i vl = _mm256_loadu_si256((__m256i*)(lower + i));
        __m256i vu = _mm256_loadu_si256((__m256i*)(upper + i));
        __m256i vv = _mm256_loadu_si256((__m256i*)(values + i));
        __m256i lo = _mm256_cmpgt_epi32(vv, vl);
        __m256i hi = _mm256_cmpgt_epi32(vu, vv);
        __m256i ok = _mm256_and_si256(lo, hi);
        if (_mm256_movemask_epi8(ok) != (int)0xFFFFFFFF) return 0;
    }
    for (int i = ((n & ~7)); i < {N}; i++)
        if (values[i] < lower[i] || values[i] > upper[i]) return 0;
    return 1;
"""

SCALAR_CHECK_BODY = """
    for (int i = 0; i < {N}; i++)
        if (values[i] < lower[i] || values[i] > upper[i]) return 0;
    return 1;
"""

BLOOM_MERGE_C = """
#include <stdint.h>
#include <string.h>
#include <immintrin.h>

// Auto-refactored bloom merge for {N} words
// Strategy: {strategy}

void bloom_merge_{N}(uint64_t *dst, const uint64_t *src) {{
{body}
}}

void bloom_merge_batch_{N}(uint64_t *dst, const uint64_t *src, int count) {{
    for (int c = 0; c < count; c++) {{
        uint64_t *d = dst;
        const uint64_t *s = src;
        {batch_body}
    }}
}}
"""

AVX2_BLOOM_BODY = """
    for (int i = 0; i + 8 <= {N}; i += 8) {
        __m256i d0 = _mm256_loadu_si256((__m256i*)(dst + i));
        __m256i s0 = _mm256_loadu_si256((__m256i*)(src + i));
        _mm256_storeu_si256((__m256i*)(dst + i), _mm256_or_si256(d0, s0));
        __m256i d1 = _mm256_loadu_si256((__m256i*)(dst + i + 4));
        __m256i s1 = _mm256_loadu_si256((__m256i*)(src + i + 4));
        _mm256_storeu_si256((__m256i*)(dst + i + 4), _mm256_or_si256(d1, s1));
    }
    for (int i = ((n & ~7)); i < {N}; i++) dst[i] |= src[i];
"""

SCALAR_BLOOM_BODY = """
    for (int i = 0; i < {N}; i++) dst[i] |= src[i];
"""

SNAP_C = """
#include <math.h>
#include <stdint.h>

const double SQRT3_2 = 0.8660254037844387;
const double SAFE_D2 = 0.25;

void snap_batch(const double *xs, const double *ys, int32_t *eas, int32_t *ebs, int n) {{
    for (int i = 0; i < n; i++) {{
        double x = xs[i], y = ys[i];
        double b_raw = y / SQRT3_2;
        double a_raw = x + b_raw / 2.0;
        int32_t ea = (int32_t)round(a_raw);
        int32_t eb = (int32_t)round(b_raw);
        double dx = x - ea + eb * 0.5;
        double dy = y - eb * SQRT3_2;
        if (dx*dx + dy*dy < SAFE_D2) {{
            eas[i] = ea; ebs[i] = eb;
            continue;  // 80.2% skip
        }}
        // Check 6 neighbors
        int32_t best_a = ea, best_b = eb;
        double best_d2 = dx*dx + dy*dy;
        int dirs[6][2] = {{{{1,0}},{{0,1}},{{-1,1}},{{-1,0}},{{0,-1}},{{1,-1}}}};
        for (int d = 0; d < 6; d++) {{
            int32_t na = ea + dirs[d][0], nb = eb + dirs[d][1];
            double ndx = x - na + nb*0.5, ndy = y - nb*SQRT3_2;
            double nd2 = ndx*ndx + ndy*ndy;
            if (nd2 < best_d2) {{ best_d2 = nd2; best_a = na; best_b = nb; }}
        }}
        eas[i] = best_a; ebs[i] = best_b;
    }}
}}
"""

NORM_C = """
#include <stdint.h>

void norm_batch(const int32_t *a, const int32_t *b, int64_t *out, int n) {{
    for (int i = 0; i < n; i++) {{
        int64_t aa = a[i], bb = b[i];
        out[i] = aa*aa - aa*bb + bb*bb;
    }}
}}
"""

FOLD_C = """
void fold_batch(double *vals, int n, double k) {{
    double sum = 0;
    for (int i = 0; i < n; i++) sum += vals[i];
    double mean = sum / n;
    for (int i = 0; i < n; i++) vals[i] = mean + k * (vals[i] - mean);
}}
"""


class RefactoringEngine:
    """
    Monitors runtime performance and JIT-compiles optimized replacements.
    """
    
    def __init__(self):
        self.refactored_libs = {}
    
    def should_refactor(self, primitive: str, call_count: int, avg_ns: float) -> bool:
        """Decide if a primitive needs refactoring."""
        thresholds = {
            "check": (100, 1000),    # >100 calls AND >1000ns avg
            "bloom_merge": (50, 500),
            "snap": (100, 10000),
            "norm": (100, 1000),
            "fold": (50, 5000),
        }
        min_calls, min_ns = thresholds.get(primitive, (1000, 10000))
        return call_count >= min_calls and avg_ns >= min_ns
    
    def refactor(self, primitive: str, workload_info: dict) -> Optional[ctypes.CDLL]:
        """Generate, compile, and load an optimized implementation."""
        print(f"  REFACTORING {primitive}...")
        
        if primitive == "check":
            return self._refactor_check(workload_info)
        elif primitive == "bloom_merge":
            return self._refactor_bloom(workload_info)
        elif primitive == "snap":
            return self._refactor_snap(workload_info)
        elif primitive == "norm":
            return self._refactor_norm(workload_info)
        elif primitive == "fold":
            return self._refactor_fold(workload_info)
        return None
    
    def _compile(self, c_code: str, name: str) -> Optional[ctypes.CDLL]:
        """Compile C code to shared library and load it."""
        c_file = REFACTOR_DIR / f"{name}.c"
        so_file = REFACTOR_DIR / f"{name}.so"
        
        c_file.write_text(c_code)
        
        result = subprocess.run(
            ['gcc', '-O2', '-mavx2', '-shared', '-fPIC', '-o', str(so_file), str(c_file), '-lm'],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode != 0:
            print(f"    Compile failed: {result.stderr[:100]}")
            return None
        
        try:
            lib = ctypes.CDLL(str(so_file))
            self.refactored_libs[name] = (lib, str(so_file))
            print(f"    Compiled → {so_file}")
            return lib
        except Exception as e:
            print(f"    Load failed: {e}")
            return None
    
    def _refactor_check(self, info: dict) -> Optional[ctypes.CDLL]:
        n = info.get("n", 16)
        calls = info.get("calls", 0)
        avg_ns = info.get("avg_ns", 9999)
        
        # Choose strategy based on workload
        if n >= 8:
            strategy = "avx2_movemask"
            body = AVX2_CHECK_BODY.replace("{N}", str(n))
            batch_body = body.replace("values", "v")
        else:
            strategy = "scalar_loop"
            body = SCALAR_CHECK_BODY.replace("{N}", str(n))
            batch_body = body.replace("values", "v")
        
        code = (CONSTRAINT_CHECK_C
            .replace("{N}", str(n))
            .replace("{strategy}", strategy)
            .replace("{calls}", str(calls))
            .replace("{avg_ns}", f"{avg_ns:.0f}")
            .replace("{body}", body)
            .replace("{batch_body}", batch_body)
        )
        
        lib = self._compile(code, f"check_{n}")
        if lib:
            func = lib.__getattr__(f"constraint_check_{n}")
            func.restype = ctypes.c_int
            func.argtypes = [ctypes.POINTER(ctypes.c_int32)] * 3
            
            # Wrap for Python use
            import numpy as np
            def c_check(lower, upper, values):
                l = lower.ctypes.data_as(ctypes.POINTER(ctypes.c_int32))
                u = upper.ctypes.data_as(ctypes.POINTER(ctypes.c_int32))
                v = values.ctypes.data_as(ctypes.POINTER(ctypes.c_int32))
                return bool(func(l, u, v))
            
            return c_check
        return None
    
    def _refactor_bloom(self, info: dict) -> Optional[ctypes.CDLL]:
        n = info.get("n", 1000)
        
        if n >= 8:
            strategy = "avx2_8wide_unrolled"
            body = AVX2_BLOOM_BODY.replace("{N}", str(n))
            batch_body = body
        else:
            strategy = "scalar_loop"
            body = SCALAR_BLOOM_BODY.replace("{N}", str(n))
            batch_body = body
        
        code = (BLOOM_MERGE_C
            .replace("{N}", str(n))
            .replace("{strategy}", strategy)
            .replace("{body}", body)
            .replace("{batch_body}", batch_body)
        )
        
        lib = self._compile(code, f"bloom_{N}")
        if lib:
            func = lib.__getattr__(f"bloom_merge_{N}")
            func.restype = None
            func.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64)]
            
            import numpy as np
            def c_bloom(dst, src):
                d = dst.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64))
                s = src.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64))
                func(d, s)
                return dst
            
            return c_bloom
        return None
    
    def _refactor_snap(self, info: dict) -> Optional[ctypes.CDLL]:
        lib = self._compile(SNAP_C, "snap_batch")
        if lib:
            func = lib.snap_batch
            func.restype = None
            func.argtypes = [
                ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
                ctypes.c_int
            ]
            
            import numpy as np
            def c_snap_batch(xs, ys):
                n = len(xs)
                eas = np.empty(n, dtype=np.int32)
                ebs = np.empty(n, dtype=np.int32)
                func(
                    xs.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                    ys.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                    eas.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                    ebs.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                    n
                )
                return eas, ebs
            
            return c_snap_batch
        return None
    
    def _refactor_norm(self, info: dict) -> Optional[ctypes.CDLL]:
        lib = self._compile(NORM_C, "norm_batch")
        if lib:
            func = lib.norm_batch
            func.restype = None
            func.argtypes = [
                ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
                ctypes.POINTER(ctypes.c_int64), ctypes.c_int
            ]
            
            import numpy as np
            def c_norm_batch(a, b):
                n = len(a)
                out = np.empty(n, dtype=np.int64)
                func(
                    a.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                    b.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                    out.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
                    n
                )
                return out
            
            return c_norm_batch
        return None
    
    def _refactor_fold(self, info: dict) -> Optional[ctypes.CDLL]:
        lib = self._compile(FOLD_C, "fold_batch")
        if lib:
            func = lib.fold_batch
            func.restype = None
            func.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_double]
            
            import numpy as np
            def c_fold(vals, k=0.577350269):
                func(
                    vals.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                    len(vals), k
                )
                return vals
            
            return c_fold
        return None


# ============================================================
# SELF-OPTIMIZING RUNTIME WITH REFACTORING
# ============================================================

class SelfOptimizingRuntime:
    """
    Full self-optimizing FLUX runtime.
    
    1. Discover capabilities
    2. Auto-bind to best available implementations
    3. Execute workload
    4. Detect hot paths
    5. JIT-compile C replacements
    6. Hot-swap bindings
    7. Save learned optimizations
    """
    
    def __init__(self):
        self.binder = None  # Will be set by initialize
        self.refactorer = RefactoringEngine()
        self.call_counts = {}
        self.call_times = {}
        self.call_args = {}  # track arg patterns for refactoring
    
    def initialize(self):
        from flux_runtime import probe_system, DynamicBinder
        self.cap = probe_system()
        print(self.cap.summary())
        
        self.binder = DynamicBinder(self.cap)
        self.binder.auto_bind()
        
        print("\nINITIAL BINDINGS:")
        for prim, source in sorted(self.binder.binding_source.items()):
            print(f"  {prim:<20s} → {source}")
    
    def execute(self, primitive: str, *args, **kwargs):
        """Execute with profiling and auto-refactoring."""
        t0 = time.perf_counter_ns()
        result = self.binder.execute(primitive, *args, **kwargs)
        t1 = time.perf_counter_ns()
        
        self.call_counts[primitive] = self.call_counts.get(primitive, 0) + 1
        elapsed = t1 - t0
        self.call_times[primitive] = self.call_times.get(primitive, 0) + elapsed
        
        # Track arg patterns
        if primitive not in self.call_args:
            self.call_args[primitive] = {"n": len(args[0]) if len(args) > 0 and hasattr(args[0], '__len__') else 0}
        
        # Check if we should refactor
        calls = self.call_counts[primitive]
        avg_ns = self.call_times[primitive] / calls
        
        if calls == 200 and self.refactorer.should_refactor(primitive, calls, avg_ns):
            print(f"\n  ⚡ HOT PATH DETECTED: {primitive} ({calls} calls, {avg_ns:.0f}ns avg)")
            print(f"  🔨 JIT-compiling C replacement...")
            
            info = {
                "calls": calls,
                "avg_ns": avg_ns,
                "n": self.call_args[primitive].get("n", 16),
            }
            
            optimized = self.refactorer.refactor(primitive, info)
            if optimized:
                # Hot-swap the binding
                self.binder.bind(primitive, optimized, f"refactored_{primitive}")
                self.call_counts[primitive] = 0  # reset counter
                self.call_times[primitive] = 0
                print(f"  ✅ Hot-swapped to refactored_{primitive}")
        
        return result


def demo_refactor():
    print("=" * 60)
    print("FLUX SELF-OPTIMIZING RUNTIME — With JIT Refactoring")
    print("=" * 60)
    
    rt = SelfOptimizingRuntime()
    rt.initialize()
    
    import numpy as np
    import random
    random.seed(42)
    np.random.seed(42)
    
    print("\n--- Workload: 1000 constraint checks (triggering refactor at 200) ---\n")
    
    lower = np.zeros(16, dtype=np.int32)
    upper = np.full(16, 100, dtype=np.int32)
    
    pass_count = 0
    for i in range(1000):
        values = np.random.randint(-50, 150, size=16, dtype=np.int32)
        result = rt.execute("check", lower, upper, values)
        if result:
            pass_count += 1
    
    print(f"\n  Checks: {pass_count}/1000 passed")
    
    # Final report
    print(f"\n--- Final Performance Report ---")
    for prim in sorted(rt.call_counts.keys()):
        calls = rt.call_counts[prim]
        total = rt.call_times[prim]
        avg = total / calls if calls > 0 else 0
        source = rt.binder.binding_source.get(prim, "unknown")
        print(f"  {prim:<20s} {calls:>6d} calls  {avg:>8.0f}ns avg  [{source}]")
    
    print(f"\n  Refactored libraries in: {REFACTOR_DIR}/")
    for f in REFACTOR_DIR.glob("*.so"):
        size = f.stat().st_size
        print(f"    {f.name}: {size} bytes")


if __name__ == "__main__":
    demo_refactor()
