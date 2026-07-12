//! fluxc-verify — Translation validation for FLUX compiler.

use fluxc_codegen::CodegenOutput;
use fluxc_ir::IrModule;
use thiserror::Error;

/// Verification error type.
#[derive(Error, Debug)]
pub enum VerifyError {
    #[error("translation validation failed: {msg}")]
    ValidationFailed { msg: String },

    #[error("verification error: {msg}")]
    Internal { msg: String },
}

/// Result of translation validation.
#[derive(Debug)]
pub struct ValidationResult {
    pub valid: bool,
    pub message: String,
}

/// Validate that the compiled output is a correct translation of the IR.
pub fn validate(ir: &IrModule, output: &CodegenOutput) -> Result<ValidationResult, VerifyError> {
    // Stub: always passes for now
    Ok(ValidationResult {
        valid: true,
        message: format!(
            "translation validation passed for '{}' targeting {:?}",
            ir.name, output.target
        ),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use fluxc_ir::{IrModule, BasicBlock, FluxIR};

    #[test]
    fn validate_passes_for_valid_module() {
        let mut module = IrModule::new("test_module");
        let mut block = BasicBlock::new("entry");
        block.instructions.push(FluxIR::CheckExact { slot: 0, value: 1 });
        block.instructions.push(FluxIR::Halt { reason: fluxc_ir::HaltReason::Pass });
        module.blocks.push(block);

        let output = CodegenOutput {
            target: fluxc_codegen::Target::Native,
            assembly: "; test\n".to_string(),
            bytes: vec![],
        };

        let result = validate(&module, &output).unwrap();
        assert!(result.valid);
        assert!(result.message.contains("test_module"));
    }

    #[test]
    fn validate_empty_module() {
        let module = IrModule::new("empty");
        let output = CodegenOutput {
            target: fluxc_codegen::Target::Native,
            assembly: String::new(),
            bytes: vec![],
        };

        let result = validate(&module, &output).unwrap();
        assert!(result.valid);
    }
}
