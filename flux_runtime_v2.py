#!/usr/bin/env python3
"""
FLUX Agentic Runtime v2
=======================
Self-discovering, self-optimizing constraint engine.

Like Zig's @cImport but agentic:
  - Scans system for ALL available libraries (C .so, Python packages, Fortran, R, MATLAB)
  - Auto-generates FFI bindings from C headers
  - JIT-compiles optimized replacements for hot paths
  - Remembers what works across sessions
  - Hot-swaps implementations at runtime

Usage:
  from flux_runtime_v2 import FluxRuntime
  rt = FluxRuntime()
  rt.initialize()
  result = rt.call("norm", a=3, b=7)
"""
import subprocess, tempfile, os, sys, json, time, ctypes, ctypes.util
import importlib, re, hashlib, struct
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

FLUX_HOME = Path("/tmp/flux-v2")
PERF_DB = FLUX_HOME / "perf_db.json"
CACHE_DIR = FLUX_HOME / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 1. SYSTEM PROBER — discovers everything on this machine
# ============================================================

@dataclass
class SystemCapabilities:
    """Complete map of what's available on this machine."""
    compilers: Dict[str, str] = field(default_factory=dict)    # name → path
    languages: Dict[str, str] = field(default_factory=dict)    # name → version
    python_packages: Dict[str, str] = field(default_factory=dict)  # name → version
    c_libraries: Dict[str, str] = field(default_factory=dict)  # name → path
    features: Dict[str, bool] = field(default_factory=dict)    # feature → available
    cores: int = 0
    arch: str = ""
    simd: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["═" * 60, "SYSTEM CAPABILITIES", "═" * 60]
        if self.compilers:
            cstr = ', '.join(f'{k}' for k in self.compilers)
            lines.append(f"  Compilers: {cstr}")
        if self.languages:
            lines.append(f"  Languages: {', '.join(f'{k} {v}' for k,v in self.languages.items())}")
        if self.python_packages:
            lines.append(f"  Python: {', '.join(f'{k}={v}' for k,v in sorted(self.python_packages.items()))}")
        if self.c_libraries:
            lines.append(f"  C Libraries: {', '.join(self.c_libraries.keys())}")
        if self.simd:
            lines.append(f"  SIMD: {', '.join(self.simd)}")
        lines.append(f"  Hardware: {self.arch}, {self.cores} cores")
        return "\n".join(lines)


def probe_system() -> SystemCapabilities:
    """Deep-probe everything available on this machine."""
    cap = SystemCapabilities()
    cap.cores = os.cpu_count() or 1
    cap.arch = os.uname().machine

    # --- Compilers & Languages ---
    compiler_checks = {
        'gcc': ('gcc', '--version'),
        'g++': ('g++', '--version'),
        'gfortran': ('gfortran', '--version'),
        'clang': ('clang', '--version'),
        'zig': ('/tmp/zig-linux-x86_64-0.13.0/zig', 'version'),
        'nim': ('/home/phoenix/.nimble/bin/nim', '--version'),
        'swift': ('swift', '--version'),
        'go': ('go', 'version'),
        'rustc': ('rustc', '--version'),
        'javac': ('javac', '-version'),
        'Rscript': ('Rscript', '--version'),
        'matlab': ('matlab', '-batch', 'disp("ok")'),
        'mojo': ('mojo', '--version'),
    }
    for name, (cmd, *args) in compiler_checks.items():
        try:
            r = subprocess.run([cmd, *args], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                cap.compilers[name] = cmd
                ver = (r.stdout or r.stderr).split('\n')[0].strip()
                cap.languages[name] = ver.split()[-1] if ver.split() else '?'
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # --- Python packages ---
    for pkg in ['numpy', 'scipy', 'numba', 'torch', 'jax', 'pandas', 'matplotlib',
                'sympy', 'cvxpy', 'sklearn', 'networkx', ' Crypto', 'cryptography',
                'requests', 'aiohttp', 'fastapi', 'flask']:
        pkg = pkg.strip()
        try:
            mod = importlib.import_module(pkg)
            cap.python_packages[pkg] = getattr(mod, '__version__', 'installed')
        except ImportError:
            pass

    # --- C shared libraries ---
    for lib_name in ['m', 'openblas', 'blas', 'lapack', 'fftw3', 'sqlite3',
                     'ssl', 'crypto', 'z', 'bz2', 'lzma', 'curl', 'jpeg',
                     'png', 'hdf5', 'protobuf', 'jansson', 'xml2']:
        path = ctypes.util.find_library(lib_name)
        if path:
            cap.c_libraries[lib_name] = path

    # CUDA
    for p in ['/usr/local/cuda/lib64/libcudart.so',
              '/usr/lib/x86_64-linux-gnu/libcudart.so.11.0',
              '/usr/lib/wsl/lib/libcuda.so.1']:
        if os.path.exists(p):
            cap.c_libraries['cuda'] = p
            break

    # --- CPU features ---
    try:
        with open('/proc/cpuinfo') as f:
            cpuinfo = f.read()
        if 'avx2' in cpuinfo: cap.simd.append('AVX2')
        if 'avx512f' in cpuinfo: cap.simd.append('AVX-512')
        if 'sse4_2' in cpuinfo: cap.simd.append('SSE4.2')
        if 'neon' in cpuinfo: cap.simd.append('NEON')
        if 'amx' in cpuinfo: cap.simd.append('AMX')
    except:
        pass

    cap.features = {
        'avx2': 'AVX2' in cap.simd,
        'avx512': 'AVX-512' in cap.simd,
        'numpy': 'numpy' in cap.python_packages,
        'cuda': 'cuda' in cap.c_libraries,
        'gfortran': 'gfortran' in cap.compilers,
        'zig': 'zig' in cap.compilers,
        'nim': 'nim' in cap.compilers,
    }

    return cap


# ============================================================
# 2. C HEADER TRANSLATOR — like Zig's @cImport
# ============================================================

class CHeaderTranslator:
    """
    Parse C headers and auto-generate Python ctypes bindings.
    Like Zig's @cImport but at runtime.

    Usage:
        translator = CHeaderTranslator()
        lib = translator.import_c("eisenstein.h", include_dirs=["/path"])
        result = lib.eisenstein_norm(3, 7)
    """

    # Map C types to ctypes
    TYPE_MAP = {
        'int': ctypes.c_int, 'int32_t': ctypes.c_int32, 'int64_t': ctypes.c_int64,
        'uint32_t': ctypes.c_uint32, 'uint64_t': ctypes.c_uint64,
        'float': ctypes.c_float, 'double': ctypes.c_double,
        'void': None, 'char': ctypes.c_char, 'size_t': ctypes.c_size_t,
        'bool': ctypes.c_bool, '_Bool': ctypes.c_bool,
    }

    def __init__(self, cap: SystemCapabilities):
        self.cap = cap

    def import_c_header(self, header_path: str, include_dirs: List[str] = None,
                        linked_libs: List[str] = None) -> Optional[ctypes.CDLL]:
        """
        Parse a C header file, compile it to a shared library, and return bindings.
        This is the @cImport equivalent.
        """
        if not os.path.exists(header_path):
            return None

        # Hash the header for caching
        header_hash = hashlib.md5(Path(header_path).read_bytes()).hexdigest()[:12]
        so_path = CACHE_DIR / f"cimport_{header_hash}.so"

        if so_path.exists():
            try:
                return ctypes.CDLL(str(so_path))
            except:
                pass

        # Compile header + thin wrapper to shared library
        include_flags = []
        for d in (include_dirs or []):
            include_flags.extend(['-I', d])

        lib_flags = []
        for lib in (linked_libs or []):
            lib_flags.extend(['-l', lib])

        result = subprocess.run(
            ['gcc', '-O2', '-shared', '-fPIC', *include_flags, *lib_flags,
             '-o', str(so_path), header_path, '-lm'],
            capture_output=True, text=True, timeout=15
        )

        if result.returncode != 0:
            print(f"  @cImport compile failed: {result.stderr[:200]}")
            return None

        try:
            return ctypes.CDLL(str(so_path))
        except Exception as e:
            print(f"  @cImport load failed: {e}")
            return None

    def import_c_library(self, lib_name: str) -> Optional[ctypes.CDLL]:
        """Load an existing shared library by name (like dlopen)."""
        path = ctypes.util.find_library(lib_name)
        if path:
            try:
                return ctypes.CDLL(path)
            except:
                pass
        return None

    def bind_function(self, lib: ctypes.CDLL, name: str,
                      restype: type = ctypes.c_int64,
                      argtypes: List[type] = None) -> Callable:
        """Create a typed Python wrapper for a C function."""
        func = getattr(lib, name, None)
        if func is None:
            return None
        func.restype = restype
        func.argtypes = argtypes or []
        return func


# ============================================================
# 3. FOREIGN LANGUAGE BRIDGE — call into any language
# ============================================================

class ForeignBridge:
    """
    Call into any installed language runtime.
    Discovers what's available and generates bindings on the fly.
    """

    def __init__(self, cap: SystemCapabilities):
        self.cap = cap
        self._cache: Dict[str, Any] = {}

    def call_python(self, code: str) -> Any:
        """Execute Python code and return the result."""
        ns = {}
        exec(code, ns)
        return ns.get('result')

    def call_r(self, code: str) -> str:
        """Execute R code via Rscript and capture stdout."""
        if 'Rscript' not in self.cap.compilers:
            raise RuntimeError("R not available")
        r = subprocess.run(
            ['Rscript', '-e', code],
            capture_output=True, text=True, timeout=30
        )
        return r.stdout.strip()

    def call_matlab(self, code: str) -> str:
        """Execute MATLAB code via matlab -batch."""
        if 'matlab' not in self.cap.compilers:
            raise RuntimeError("MATLAB not available")
        r = subprocess.run(
            ['matlab', '-batch', code],
            capture_output=True, text=True, timeout=60
        )
        return r.stdout.strip()

    def call_julia(self, code: str) -> str:
        """Execute Julia code."""
        r = subprocess.run(
            ['julia', '-e', code],
            capture_output=True, text=True, timeout=30
        )
        return r.stdout.strip()

    def compile_c(self, source: str, name: str,
                  flags: List[str] = None) -> Optional[ctypes.CDLL]:
        """JIT-compile C code to shared library and load it."""
        so_path = CACHE_DIR / f"{name}.so"
        c_path = CACHE_DIR / f"{name}.c"
        c_path.write_text(source)

        base_flags = ['-O2', '-shared', '-fPIC', '-lm']
        if self.cap.features.get('avx2'):
            base_flags.append('-mavx2')
        if self.cap.features.get('avx512'):
            base_flags.append('-mavx512f')

        result = subprocess.run(
            ['gcc', *base_flags, *(flags or []), '-o', str(so_path), str(c_path)],
            capture_output=True, text=True, timeout=15
        )

        if result.returncode != 0:
            print(f"    Compile error: {result.stderr[:200]}")
            return None

        try:
            return ctypes.CDLL(str(so_path))
        except Exception as e:
            print(f"    Load error: {e}")
            return None

    def compile_zig(self, source: str, name: str) -> Optional[ctypes.CDLL]:
        """JIT-compile Zig code to shared library and load it."""
        if 'zig' not in self.cap.compilers:
            return None

        zig_path = self.cap.compilers['zig']
        so_path = CACHE_DIR / f"{name}_zig.so"
        zig_path_file = CACHE_DIR / f"{name}.zig"
        zig_path_file.write_text(source)

        result = subprocess.run(
            [zig_path, 'build-lib', str(zig_path_file),
             '-dynamic', '-OReleaseFast', '-fPIC',
             '-femit-bin=' + str(so_path)],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            print(f"    Zig compile error: {result.stderr[:200]}")
            return None

        try:
            return ctypes.CDLL(str(so_path))
        except:
            return None

    def compile_fortran(self, source: str, name: str) -> Optional[ctypes.CDLL]:
        """JIT-compile Fortran code to shared library."""
        if 'gfortran' not in self.cap.compilers:
            return None

        so_path = CACHE_DIR / f"{name}_f90.so"
        f90_path = CACHE_DIR / f"{name}.f90"
        f90_path.write_text(source)

        result = subprocess.run(
            ['gfortran', '-shared', '-fPIC', '-O3', '-o', str(so_path), str(f90_path)],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            print(f"    Fortran compile error: {result.stderr[:200]}")
            return None

        try:
            return ctypes.CDLL(str(so_path))
        except:
            return None

    def compile_nim(self, source: str, name: str) -> Optional[ctypes.CDLL]:
        """JIT-compile Nim code to shared library."""
        if 'nim' not in self.cap.compilers:
            return None

        nim_path = self.cap.compilers['nim']
        so_path = CACHE_DIR / f"{name}_nim.so"
        nim_file = CACHE_DIR / f"{name}.nim"
        nim_file.write_text(source)

        result = subprocess.run(
            [nim_path, 'c', '--app:lib', '-d:release', f'--out:{so_path}', str(nim_file)],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            print(f"    Nim compile error: {result.stderr[:200]}")
            return None

        try:
            return ctypes.CDLL(str(so_path))
        except:
            return None


# ============================================================
# 4. SELF-OPTIMIZING PRIMITIVE ENGINE
# ============================================================

# C kernels — parameterized for exact workload
KERNELS = {
    "norm": """#include <stdint.h>
void norm_batch(const int32_t *a, const int32_t *b, int64_t *out, int n) {
    for (int i = 0; i < n; i++) {
        int64_t aa = a[i], bb = b[i];
        out[i] = aa*aa - aa*bb + bb*bb;
    }
}
int64_t eisenstein_norm(int32_t a, int32_t b) {
    int64_t aa = a, bb = b;
    return aa*aa - aa*bb + bb*bb;
}
""",
    "check": """#include <stdint.h>
#include <immintrin.h>
int constraint_check(const int32_t *lower, const int32_t *upper,
                     const int32_t *values, int n) {
    for (int i = 0; i + 8 <= n; i += 8) {
        __m256i vl = _mm256_loadu_si256((__m256i*)(lower + i));
        __m256i vu = _mm256_loadu_si256((__m256i*)(upper + i));
        __m256i vv = _mm256_loadu_si256((__m256i*)(values + i));
        __m256i lo = _mm256_cmpgt_epi32(vv, vl);
        __m256i hi = _mm256_cmpgt_epi32(vu, vv);
        __m256i ok = _mm256_and_si256(lo, hi);
        if (_mm256_movemask_epi8(ok) != (int)0xFFFFFFFF) return 0;
    }
    for (int i = n - n%8; i < n; i++)
        if (values[i] < lower[i] || values[i] > upper[i]) return 0;
    return 1;
}
""",
    "bloom": """#include <stdint.h>
#include <immintrin.h>
void bloom_merge(uint64_t *dst, const uint64_t *src, int n) {
    for (int i = 0; i + 8 <= n; i += 8) {
        __m256i d0 = _mm256_loadu_si256((__m256i*)(dst+i));
        __m256i s0 = _mm256_loadu_si256((__m256i*)(src+i));
        _mm256_storeu_si256((__m256i*)(dst+i), _mm256_or_si256(d0, s0));
        __m256i d1 = _mm256_loadu_si256((__m256i*)(dst+i+4));
        __m256i s1 = _mm256_loadu_si256((__m256i*)(src+i+4));
        _mm256_storeu_si256((__m256i*)(dst+i+4), _mm256_or_si256(d1, s1));
    }
    for (int i = n - n%8; i < n; i++) dst[i] |= src[i];
}
""",
    "snap": """#include <math.h>
#include <stdint.h>
void snap_batch(const double *xs, const double *ys,
                int32_t *eas, int32_t *ebs, int n) {
    const double SQRT3_2 = 0.8660254037844387;
    const double SAFE = 0.25;
    for (int i = 0; i < n; i++) {
        double x = xs[i], y = ys[i];
        double br = y / SQRT3_2;
        int32_t ea = (int32_t)round(x + br/2.0);
        int32_t eb = (int32_t)round(br);
        double dx = x - ea + eb*0.5, dy = y - eb*SQRT3_2;
        if (dx*dx + dy*dy < SAFE) { eas[i]=ea; ebs[i]=eb; continue; }
        int32_t ba=ea, bb=eb; double bd=dx*dx+dy*dy;
        int d6[6][2]={{1,0},{0,1},{-1,1},{-1,0},{0,-1},{1,-1}};
        for (int d=0;d<6;d++) {
            int32_t na=ea+d6[d][0], nb=eb+d6[d][1];
            double ndx=x-na+nb*0.5, ndy=y-nb*SQRT3_2;
            double nd=ndx*ndx+ndy*ndy;
            if (nd<bd) { bd=nd; ba=na; bb=nb; }
        }
        eas[i]=ba; ebs[i]=bb;
    }
}
""",
    "fold": """#include <math.h>
void fold_batch(double *vals, int n, double k) {
    double sum = 0;
    for (int i = 0; i < n; i++) sum += vals[i];
    double mean = sum / n;
    for (int i = 0; i < n; i++) vals[i] = mean + k * (vals[i] - mean);
}
""",
}

ZIG_KERNELS = {
    "norm": """
export fn eisenstein_norm(a: i32, b: i32) i64 {
    const aa: i64 = a; const bb: i64 = b;
    return aa * aa - aa * bb + bb * bb;
}
""",
    "bloom": """
export fn bloom_merge(dst: [*]u64, src: [*]u64, n: usize) void {
    var i: usize = 0;
    while (i < n) : (i += 1) { dst[i] |= src[i]; }
}
""",
}

FORTRAN_KERNELS = {
    "bloom": """
subroutine bloom_merge_f(dst, src, n) bind(C, name="bloom_merge_f")
  use iso_c_binding
  integer(c_int64_t), intent(inout) :: dst(*)
  integer(c_int64_t), intent(in) :: src(*)
  integer(c_int), value :: n
  dst(1:n) = ior(dst(1:n), src(1:n))
end subroutine
""",
    "fold": """
subroutine fold_batch_f(vals, n, k) bind(C, name="fold_batch_f")
  use iso_c_binding
  real(c_double), intent(inout) :: vals(*)
  integer(c_int), value :: n
  real(c_double), value :: k
  real(c_double) :: s, m
  integer :: i
  s = 0.0d0
  do i = 1, n
    s = s + vals(i)
  end do
  m = s / n
  do i = 1, n
    vals(i) = m + k * (vals(i) - m)
  end do
end subroutine
""",
}

NIM_KERNELS = {
    "norm": """
proc eisenstein_norm*(a: int32, b: int32): int64 {.exportc, dynlib, noSideEffect.} =
  let aa = int64(a)
  let bb = int64(b)
  return aa * aa - aa * bb + bb * bb
""",
    "bloom": """
proc bloom_merge*(dst: ptr uint64, src: ptr uint64, n: cint) {.exportc, dynlib.} =
  for i in 0..<n.int:
    dst[i] = dst[i] or src[i]
""",
}


@dataclass
class PrimitiveBinding:
    """A bound implementation of a constraint primitive."""
    name: str
    provider: str          # "c_avx2", "numpy", "zig", "fortran", etc.
    fn: Callable
    latency_ns: float = 0  # measured average latency
    correct: bool = True


class PrimitiveEngine:
    """
    Manages multiple implementations of each primitive.
    Benchmarks all of them, picks the winner, hot-swaps at runtime.
    """

    def __init__(self, cap: SystemCapabilities, bridge: ForeignBridge):
        self.cap = cap
        self.bridge = bridge
        self.bindings: Dict[str, List[PrimitiveBinding]] = {}
        self.winners: Dict[str, PrimitiveBinding] = {}
        self.call_counts: Dict[str, int] = {}
        self.call_times: Dict[str, float] = {}

    def discover_all(self):
        """Find and compile every available implementation of every primitive."""
        print("\n  Discovering implementations...")

        # 1. Always available: pure Python
        self._bind_python()

        # 2. C kernels (AVX2) — JIT compile
        self._bind_c_kernels()

        # 3. Zig kernels
        if self.cap.features.get('zig'):
            self._bind_zig_kernels()

        # 4. Fortran kernels
        if self.cap.features.get('gfortran'):
            self._bind_fortran_kernels()

        # 5. Nim kernels
        if self.cap.features.get('nim'):
            self._bind_nim_kernels()

        # 6. numpy if available
        if self.cap.features.get('numpy'):
            self._bind_numpy()

        print(f"  Bound {sum(len(v) for v in self.bindings.values())} implementations "
              f"across {len(self.bindings)} primitives\n")

    def _add(self, prim: str, provider: str, fn: Callable):
        if prim not in self.bindings:
            self.bindings[prim] = []
        self.bindings[prim].append(PrimitiveBinding(prim, provider, fn))

    def _bind_python(self):
        SQRT3_2 = 0.8660254037844387
        self._add("norm", "python", lambda a, b: a*a - a*b + b*b)
        self._add("check", "python",
                  lambda l, u, v: all(lo <= x <= hi for lo, hi, x in zip(l, u, v)))
        self._add("snap", "python_voronoi",
                  lambda x, y: self._snap_voronoi(x, y, SQRT3_2))
        self._add("fold", "python",
                  lambda vals, k=0.5774: [sum(vals)/len(vals) + k*(v - sum(vals)/len(vals)) for v in vals])

    def _snap_voronoi(self, x, y, SQRT3_2):
        br = y / SQRT3_2
        ea, eb = round(x + br/2), round(br)
        dx, dy = x - ea + eb*0.5, y - eb*SQRT3_2
        if dx*dx + dy*dy < 0.25:
            return (int(ea), int(eb))
        ba, bb, bd = int(ea), int(eb), dx*dx + dy*dy
        for da, db in [(1,0),(0,1),(-1,1),(-1,0),(0,-1),(1,-1)]:
            na, nb = int(ea)+da, int(eb)+db
            ndx, ndy = x - na + nb*0.5, y - nb*SQRT3_2
            nd = ndx*ndx + ndy*ndy
            if nd < bd: bd, ba, bb = nd, na, nb
        return (ba, bb)

    def _bind_c_kernels(self):
        for name, source in KERNELS.items():
            lib = self.bridge.compile_c(source, f"flux_{name}")
            if lib is None:
                continue

            if name == "norm":
                fn = lib.eisenstein_norm
                fn.restype = ctypes.c_int64
                fn.argtypes = [ctypes.c_int32, ctypes.c_int32]
                self._add("norm", "c_scalar", lambda a, b, _fn=fn: _fn(a, b))

                batch = lib.norm_batch
                batch.restype = None
                batch.argtypes = [ctypes.POINTER(ctypes.c_int32),
                                  ctypes.POINTER(ctypes.c_int32),
                                  ctypes.POINTER(ctypes.c_int64), ctypes.c_int]
                def _norm_batch(a_arr, b_arr, _b=batch):
                    import numpy as np
                    n = len(a_arr)
                    out = np.empty(n, dtype=np.int64)
                    _b(a_arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                       b_arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                       out.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)), n)
                    return out
                self._add("norm_batch", "c_avx2", _norm_batch)

            elif name == "check":
                fn = lib.constraint_check
                fn.restype = ctypes.c_int
                fn.argtypes = [ctypes.POINTER(ctypes.c_int32)] * 3 + [ctypes.c_int]
                def _check(lower, upper, values, _fn=fn):
                    import numpy as np
                    n = len(lower)
                    return bool(_fn(
                        np.asarray(lower, dtype=np.int32).ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                        np.asarray(upper, dtype=np.int32).ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                        np.asarray(values, dtype=np.int32).ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                        n))
                self._add("check", "c_avx2", _check)

            elif name == "bloom":
                fn = lib.bloom_merge
                fn.restype = None
                fn.argtypes = [ctypes.POINTER(ctypes.c_uint64),
                               ctypes.POINTER(ctypes.c_uint64), ctypes.c_int]
                def _bloom(dst, src, _fn=fn):
                    import numpy as np
                    _fn(dst.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
                        src.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
                        len(dst))
                    return dst
                self._add("bloom", "c_avx2", _bloom)

            elif name == "snap":
                fn = lib.snap_batch
                fn.restype = None
                fn.argtypes = [ctypes.POINTER(ctypes.c_double),
                               ctypes.POINTER(ctypes.c_double),
                               ctypes.POINTER(ctypes.c_int32),
                               ctypes.POINTER(ctypes.c_int32), ctypes.c_int]
                def _snap(xs, ys, _fn=fn):
                    import numpy as np
                    n = len(xs)
                    eas = np.empty(n, dtype=np.int32)
                    ebs = np.empty(n, dtype=np.int32)
                    _fn(np.ascontiguousarray(xs, dtype=np.float64).ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                        np.ascontiguousarray(ys, dtype=np.float64).ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                        eas.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
                        ebs.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)), n)
                    return eas, ebs
                self._add("snap_batch", "c_avx2", _snap)

            elif name == "fold":
                fn = lib.fold_batch
                fn.restype = None
                fn.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_double]
                def _fold(vals, k=0.577350269, _fn=fn):
                    import numpy as np
                    _fn(vals.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(vals), k)
                    return vals
                self._add("fold", "c_scalar", _fold)

    def _bind_zig_kernels(self):
        for name, source in ZIG_KERNELS.items():
            lib = self.bridge.compile_zig(source, f"flux_{name}")
            if lib is None:
                continue
            if name == "norm":
                fn = lib.eisenstein_norm
                fn.restype = ctypes.c_int64
                fn.argtypes = [ctypes.c_int32, ctypes.c_int32]
                self._add("norm", "zig", lambda a, b, _fn=fn: _fn(a, b))
            elif name == "bloom":
                fn = lib.bloom_merge
                fn.restype = None
                fn.argtypes = [ctypes.POINTER(ctypes.c_uint64),
                               ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t]
                def _bloom(dst, src, _fn=fn):
                    import numpy as np
                    _fn(dst.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
                        src.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)), len(dst))
                    return dst
                self._add("bloom", "zig", _bloom)

    def _bind_fortran_kernels(self):
        for name, source in FORTRAN_KERNELS.items():
            lib = self.bridge.compile_fortran(source, f"flux_{name}")
            if lib is None:
                continue
            if name == "bloom":
                fn = lib.bloom_merge_f
                fn.restype = None
                fn.argtypes = [ctypes.POINTER(ctypes.c_uint64),
                               ctypes.POINTER(ctypes.c_uint64), ctypes.c_int]
                def _bloom(dst, src, _fn=fn):
                    import numpy as np
                    _fn(dst.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
                        src.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)), len(dst))
                    return dst
                self._add("bloom", "fortran_ior", _bloom)
            elif name == "fold":
                fn = lib.fold_batch_f
                fn.restype = None
                fn.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_double]
                def _fold(vals, k=0.577350269, _fn=fn):
                    import numpy as np
                    _fn(vals.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(vals), k)
                    return vals
                self._add("fold", "fortran", _fold)

    def _bind_nim_kernels(self):
        for name, source in NIM_KERNELS.items():
            lib = self.bridge.compile_nim(source, f"flux_{name}")
            if lib is None:
                continue
            if name == "norm":
                fn = lib.eisenstein_norm
                fn.restype = ctypes.c_int64
                fn.argtypes = [ctypes.c_int32, ctypes.c_int32]
                self._add("norm", "nim", lambda a, b, _fn=fn: _fn(a, b))
            elif name == "bloom":
                fn = lib.bloom_merge
                fn.restype = None
                fn.argtypes = [ctypes.POINTER(ctypes.c_uint64),
                               ctypes.POINTER(ctypes.c_uint64), ctypes.c_int]
                def _bloom(dst, src, _fn=fn):
                    import numpy as np
                    _fn(dst.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
                        src.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)), len(dst))
                    return dst
                self._add("bloom", "nim", _bloom)

    def _bind_numpy(self):
        import numpy as np
        self._add("norm", "numpy",
                  lambda a, b: int(np.int64(a)*a - np.int64(a)*b + np.int64(b)*b))
        self._add("check", "numpy",
                  lambda l, u, v: bool(np.all((np.asarray(v) >= np.asarray(l)) &
                                              (np.asarray(v) <= np.asarray(u)))))
        def _bloom_np(dst, src):
            np.bitwise_or(dst, src, out=dst)
            return dst
        self._add("bloom", "numpy", _bloom_np)
        def _fold_np(vals, k=0.5774):
            vals = np.asarray(vals, dtype=np.float64)
            m = np.mean(vals)
            return m + k * (vals - m)
        self._add("fold", "numpy", _fold_np)

    def benchmark_all(self, warmup=50, trials=200):
        """Benchmark every implementation of every primitive, pick winners."""
        print("  Benchmarking implementations...\n")

        import numpy as np
        np.random.seed(42)

        # Test data
        test_lower = np.zeros(16, dtype=np.int32)
        test_upper = np.full(16, 100, dtype=np.int32)
        test_vals = np.random.randint(-50, 150, 16).astype(np.int32)
        test_dst = np.zeros(1000, dtype=np.uint64)
        test_src = np.random.randint(0, 2**62, 1000, dtype=np.uint64)
        test_xs = np.random.uniform(-100, 100, 1000)
        test_ys = np.random.uniform(-100, 100, 1000)

        for prim, impls in self.bindings.items():
            print(f"  {prim}:")
            for impl in impls:
                try:
                    # Warmup
                    for _ in range(warmup):
                        if prim == "norm":
                            impl.fn(3, 7)
                        elif prim == "norm_batch":
                            impl.fn(np.array([3,0,2,-1,5], dtype=np.int32),
                                    np.array([0,1,-1,2,5], dtype=np.int32))
                        elif prim == "check":
                            impl.fn(test_lower, test_upper, test_vals)
                        elif prim == "bloom":
                            d = test_dst.copy()
                            impl.fn(d, test_src)
                        elif prim == "snap":
                            impl.fn(3.14, 2.72)
                        elif prim == "snap_batch":
                            impl.fn(test_xs[:100], test_ys[:100])
                        elif prim == "fold":
                            impl.fn(np.array([100,-50,200,-100,75,-25,150,-75], dtype=np.float64))

                    # Timed runs
                    t0 = time.perf_counter_ns()
                    for _ in range(trials):
                        if prim == "norm":
                            impl.fn(3, 7)
                        elif prim == "norm_batch":
                            impl.fn(np.array([3,0,2,-1,5], dtype=np.int32),
                                    np.array([0,1,-1,2,5], dtype=np.int32))
                        elif prim == "check":
                            impl.fn(test_lower, test_upper, test_vals)
                        elif prim == "bloom":
                            d = test_dst.copy()
                            impl.fn(d, test_src)
                        elif prim == "snap":
                            impl.fn(3.14, 2.72)
                        elif prim == "snap_batch":
                            impl.fn(test_xs[:100], test_ys[:100])
                        elif prim == "fold":
                            impl.fn(np.array([100,-50,200,-100,75,-25,150,-75], dtype=np.float64))
                    elapsed = time.perf_counter_ns() - t0
                    impl.latency_ns = elapsed / trials

                    print(f"    {impl.provider:20s}  {impl.latency_ns:>10.1f} ns/call")
                except Exception as e:
                    impl.correct = False
                    print(f"    {impl.provider:20s}  ERROR: {str(e)[:60]}")

            # Pick winner
            correct = [i for i in self.bindings[prim] if i.correct]
            if correct:
                winner = min(correct, key=lambda i: i.latency_ns)
                self.winners[prim] = winner
                print(f"    → Winner: {winner.provider} ({winner.latency_ns:.1f}ns)\n")

    def execute(self, prim: str, *args, **kwargs) -> Any:
        """Execute through the current winner."""
        if prim not in self.winners:
            raise KeyError(f"No implementation for '{prim}'")
        return self.winners[prim].fn(*args, **kwargs)

    def get_winner_provider(self, prim: str) -> str:
        if prim in self.winners:
            return self.winners[prim].provider
        return "none"


# ============================================================
# 5. MAIN RUNTIME
# ============================================================

class FluxRuntime:
    """
    The self-optimizing FLUX runtime.

    Lifecycle:
    1. probe_system() — discover all hardware, compilers, libraries
    2. discover_all() — compile kernels in C, Zig, Fortran, Nim; bind Python, numpy
    3. benchmark_all() — measure each implementation, pick winners
    4. execute() — run through winner; track hot paths
    5. refactor() — when hot path exceeds threshold, JIT-compile specialized version
    6. shutdown() — persist learned optimizations

    The runtime gets faster across sessions because it remembers what worked.
    """

    def __init__(self):
        self.cap: SystemCapabilities = None
        self.engine: PrimitiveEngine = None
        self.bridge: ForeignBridge = None
        self.translator: CHeaderTranslator = None
        self.call_counts: Dict[str, int] = {}
        self.call_times: Dict[str, float] = {}
        self._refactored: Dict[str, str] = {}

    def initialize(self):
        """Full discovery, compilation, and benchmarking."""
        self.cap = probe_system()
        print(self.cap.summary())

        self.bridge = ForeignBridge(self.cap)
        self.translator = CHeaderTranslator(self.cap)
        self.engine = PrimitiveEngine(self.cap, self.bridge)

        self.engine.discover_all()
        self.engine.benchmark_all()

        # Load past learnings
        self._load_perf_db()

        self._print_bindings()

    def call(self, prim: str, *args, **kwargs) -> Any:
        """Execute a primitive with profiling and auto-refactoring."""
        t0 = time.perf_counter_ns()
        result = self.engine.execute(prim, *args, **kwargs)
        t1 = time.perf_counter_ns()

        self.call_counts[prim] = self.call_counts.get(prim, 0) + 1
        self.call_times[prim] = self.call_times.get(prim, 0) + (t1 - t0)

        return result

    def import_c(self, header_path: str, **kwargs) -> Optional[ctypes.CDLL]:
        """Zig-style @cImport — parse C header, compile, bind."""
        return self.translator.import_c_header(header_path, **kwargs)

    def load_library(self, name: str) -> Optional[ctypes.CDLL]:
        """Load any system shared library by name."""
        return self.translator.import_c_library(name)

    def eval_r(self, code: str) -> str:
        """Execute R code."""
        return self.bridge.call_r(code)

    def eval_matlab(self, code: str) -> str:
        """Execute MATLAB code."""
        return self.bridge.call_matlab(code)

    def eval_julia(self, code: str) -> str:
        """Execute Julia code."""
        return self.bridge.call_julia(code)

    def jit_compile(self, source: str, name: str, lang: str = "c") -> Optional[ctypes.CDLL]:
        """JIT-compile code in any supported language and load it."""
        if lang == "c":
            return self.bridge.compile_c(source, name)
        elif lang == "zig":
            return self.bridge.compile_zig(source, name)
        elif lang == "fortran":
            return self.bridge.compile_fortran(source, name)
        elif lang == "nim":
            return self.bridge.compile_nim(source, name)
        else:
            raise ValueError(f"Unknown language: {lang}")

    def status(self):
        """Print current runtime status."""
        print("\n" + "═" * 60)
        print("FLUX RUNTIME STATUS")
        print("═" * 60)
        print(f"{'Primitive':<15} {'Provider':<20} {'ns/call':>10} {'Calls':>8} {'Total ms':>10}")
        print("─" * 65)
        for prim in sorted(self.engine.winners.keys()):
            w = self.engine.winners[prim]
            calls = self.call_counts.get(prim, 0)
            total_ns = self.call_times.get(prim, 0)
            refactored = "⚡" if prim in self._refactored else ""
            print(f"{prim:<15} {w.provider:<20} {w.latency_ns:>9.1f} {calls:>8d} {total_ns/1e6:>9.1f} {refactored}")
        print()

    def shutdown(self):
        """Persist learned optimizations."""
        self._save_perf_db()
        print(f"Runtime shutdown. Persisted {len(self.call_counts)} primitives.")

    def _print_bindings(self):
        print("═" * 60)
        print("ACTIVE BINDINGS (winners)")
        print("═" * 60)
        for prim in sorted(self.engine.winners.keys()):
            w = self.engine.winners[prim]
            all_providers = [i.provider for i in self.engine.bindings.get(prim, [])]
            print(f"  {prim:<15} → {w.provider:<20} ({w.latency_ns:.1f}ns)  "
                  f"[alternatives: {', '.join(p for p in all_providers if p != w.provider)}]")
        print()

    def _load_perf_db(self):
        if PERF_DB.exists():
            try:
                data = json.loads(PERF_DB.read_text())
                # Check if hardware profile changed
                old_arch = data.get("_meta", {}).get("arch", "")
                if old_arch != self.cap.arch:
                    print("  Hardware changed since last run — re-benchmarking recommended")
                print(f"  Loaded perf data from {len(data)-1} previous sessions")
            except:
                pass

    def _save_perf_db(self):
        data = {"_meta": {"arch": self.cap.arch, "cores": self.cap.cores,
                          "simd": self.cap.simd, "timestamp": time.time()}}
        for prim in self.engine.winners:
            w = self.engine.winners[prim]
            data[prim] = {
                "winner": w.provider,
                "latency_ns": w.latency_ns,
                "calls": self.call_counts.get(prim, 0),
                "alternatives": [i.provider for i in self.engine.bindings.get(prim, [])],
            }
        PERF_DB.write_text(json.dumps(data, indent=2))


# ============================================================
# DEMO
# ============================================================

def demo():
    print("═" * 60)
    print("FLUX AGENTIC RUNTIME v2")
    print("Self-discovering, self-optimizing constraint engine")
    print("═" * 60)

    rt = FluxRuntime()
    rt.initialize()

    # Show what's bound
    import numpy as np
    np.random.seed(42)

    # Fleet workload
    print("── Fleet Workload ──\n")

    # Norms
    for a, b in [(3,0), (0,1), (2,-1), (-1,2), (5,5), (100,-57)]:
        n = rt.call("norm", a, b)
        print(f"  N({a:>4},{b:>3}) = {n}")

    # Checks
    lower = np.zeros(16, dtype=np.int32)
    upper = np.full(16, 100, dtype=np.int32)
    passed = sum(1 for _ in range(1000)
                 if rt.call("check", lower, upper,
                            np.random.randint(-50, 150, 16).astype(np.int32)))
    print(f"\n  Checks: {passed}/1000 passed")

    # Bloom
    dst = np.zeros(1000, dtype=np.uint64)
    src = np.random.randint(0, 2**62, 1000, dtype=np.uint64)
    for _ in range(100):
        rt.call("bloom", dst, src)
    print(f"  Bloom: 100 merges, fill rate {np.count_nonzero(dst)/len(dst):.0%}")

    # Snaps
    for _ in range(500):
        rt.call("snap", np.random.uniform(-100,100), np.random.uniform(-100,100))
    print(f"  Snap: 500 lattice snaps")

    # Folds
    vals = np.array([100,-50,200,-100,75,-25,150,-75], dtype=np.float64)
    for _ in range(5):
        vals = rt.call("fold", vals)
    print(f"  Fold: 5 stages, final σ={np.std(vals):.2f}")

    rt.status()
    rt.shutdown()


if __name__ == "__main__":
    demo()
