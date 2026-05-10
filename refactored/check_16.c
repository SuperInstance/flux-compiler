
#include <stdint.h>
#include <string.h>
#include <immintrin.h>

// Auto-refactored constraint check for n=16 elements
// Strategy: avx2_movemask
// Hot path: called 200 times, avg {avg_ns:.0f}ns in Python

int constraint_check_16(const int32_t *lower, const int32_t *upper, const int32_t *values) {{

    for (int i = 0; i + 8 <= 16; i += 8) {
        __m256i vl = _mm256_loadu_si256((__m256i*)(lower + i));
        __m256i vu = _mm256_loadu_si256((__m256i*)(upper + i));
        __m256i vv = _mm256_loadu_si256((__m256i*)(values + i));
        __m256i lo = _mm256_cmpgt_epi32(vv, vl);
        __m256i hi = _mm256_cmpgt_epi32(vu, vv);
        __m256i ok = _mm256_and_si256(lo, hi);
        if (_mm256_movemask_epi8(ok) != (int)0xFFFFFFFF) return 0;
    }
    for (int i = ((n & ~7)); i < 16; i++)
        if (values[i] < lower[i] || values[i] > upper[i]) return 0;
    return 1;

}}

// Batch: check multiple constraint sets at once
int constraint_check_batch_16(const int32_t *lower, const int32_t *upper,
                                const int32_t *values, int batch_size, int stride) {{
    for (int b = 0; b < batch_size; b++) {{
        const int32_t *v = values + b * stride;
        
    for (int i = 0; i + 8 <= 16; i += 8) {
        __m256i vl = _mm256_loadu_si256((__m256i*)(lower + i));
        __m256i vu = _mm256_loadu_si256((__m256i*)(upper + i));
        __m256i vv = _mm256_loadu_si256((__m256i*)(v + i));
        __m256i lo = _mm256_cmpgt_epi32(vv, vl);
        __m256i hi = _mm256_cmpgt_epi32(vu, vv);
        __m256i ok = _mm256_and_si256(lo, hi);
        if (_mm256_movemask_epi8(ok) != (int)0xFFFFFFFF) return 0;
    }
    for (int i = ((n & ~7)); i < 16; i++)
        if (v[i] < lower[i] || v[i] > upper[i]) return 0;
    return 1;

    }}
    return 1;  // all passed
}}
