//! fluxc-codegen — Code generation for FLUX IR.
//!
//! Supports multiple targets via feature flags: avx512, cuda, wasm, ebpf, riscv.

use fluxc_ir::IrModule;
use thiserror::Error;

/// Code generation error type.
#[derive(Error, Debug)]
pub enum CodegenError {
    #[error("unsupported target: {target}")]
    UnsupportedTarget { target: String },

    #[error("code generation failed: {msg}")]
    Failed { msg: String },
}

/// Supported code generation targets.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Target {
    /// Generic native code.
    Native,
    /// AVX-512 vectorized.
    Avx512,
    /// NVIDIA CUDA kernel.
    Cuda,
    /// WebAssembly module.
    Wasm,
    /// eBPF program.
    Ebpf,
    /// RISC-V bare metal.
    Riscv,
}

impl Target {
    /// Parse a target from a string.
    pub fn from_str(s: &str) -> Result<Self, CodegenError> {
        match s {
            "native" => Ok(Target::Native),
            #[cfg(feature = "avx512")]
            "avx512" => Ok(Target::Avx512),
            #[cfg(feature = "cuda")]
            "cuda" => Ok(Target::Cuda),
            #[cfg(feature = "wasm")]
            "wasm" => Ok(Target::Wasm),
            #[cfg(feature = "ebpf")]
            "ebpf" => Ok(Target::Ebpf),
            #[cfg(feature = "riscv")]
            "riscv" => Ok(Target::Riscv),
            other => Err(CodegenError::UnsupportedTarget {
                target: other.to_string(),
            }),
        }
    }
}

/// Generated output.
#[derive(Debug, Clone)]
pub struct CodegenOutput {
    pub target: Target,
    pub assembly: String,
    pub bytes: Vec<u8>,
}

/// Generate code for the given IR module targeting `target`.
pub fn generate(module: &IrModule, target: Target) -> Result<CodegenOutput, CodegenError> {
    match target {
        Target::Native => generate_native(module),
        Target::Avx512 => generate_native(module), // stub: falls through
        Target::Cuda => generate_native(module),
        Target::Wasm => generate_native(module),
        Target::Ebpf => generate_native(module),
        Target::Riscv => generate_native(module),
    }
}

fn generate_native(module: &IrModule) -> Result<CodegenOutput, CodegenError> {
    // Stub: emit placeholder assembly
    let assembly = format!("; FLUX compiled: {}\n; TODO: actual codegen\n", module.name);
    Ok(CodegenOutput {
        target: Target::Native,
        assembly,
        bytes: Vec::new(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use fluxc_ir::IrModule;

    #[test]
    fn generate_native_produces_output() {
        let module = IrModule::new("test_prog");
        let result = generate(&module, Target::Native).unwrap();
        assert_eq!(result.target, Target::Native);
        assert!(result.assembly.contains("test_prog"));
    }

    #[test]
    fn target_from_str_native() {
        let target = Target::from_str("native").unwrap();
        assert_eq!(target, Target::Native);
    }

    #[test]
    fn target_from_str_unknown_errors() {
        let result = Target::from_str("unknown_target");
        assert!(result.is_err());
    }
}
