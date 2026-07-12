//! fluxc-ir — FLUX intermediate representation.

use thiserror::Error;

/// IR error type.
#[derive(Error, Debug)]
pub enum IrError {
    #[error("invalid IR: {msg}")]
    Invalid { msg: String },

    #[error("verification failed: {msg}")]
    VerificationFailed { msg: String },
}

/// Reason for a halt instruction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HaltReason {
    /// Constraint satisfied, normal termination.
    Pass,
    /// Constraint violated.
    Violation { slot: u8 },
    /// Unreachable code reached.
    Unreachable,
}

/// A FLUX intermediate representation instruction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FluxIR {
    /// Check that slot value is in `[lo, hi]`.
    CheckRange { slot: u8, lo: i64, hi: i64 },
    /// Check that slot bits match `mask`.
    CheckDomain { slot: u8, mask: u64 },
    /// Check that slot equals `value`.
    CheckExact { slot: u8, value: i64 },
    /// Logical AND of the two preceding results.
    And,
    /// Logical OR of the two preceding results.
    Or,
    /// Negate the preceding result.
    Not,
    /// Halt execution with a reason.
    Halt { reason: HaltReason },
    /// No-op / placeholder.
    Nop,
}

/// A basic block of IR instructions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BasicBlock {
    pub label: String,
    pub instructions: Vec<FluxIR>,
}

impl BasicBlock {
    pub fn new(label: &str) -> Self {
        Self {
            label: label.to_string(),
            instructions: Vec::new(),
        }
    }
}

/// A complete IR module.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IrModule {
    pub name: String,
    pub blocks: Vec<BasicBlock>,
}

impl IrModule {
    pub fn new(name: &str) -> Self {
        Self {
            name: name.to_string(),
            blocks: Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn basic_block_new() {
        let block = BasicBlock::new("entry");
        assert_eq!(block.label, "entry");
        assert!(block.instructions.is_empty());
    }

    #[test]
    fn ir_module_new() {
        let module = IrModule::new("test");
        assert_eq!(module.name, "test");
        assert!(module.blocks.is_empty());
    }

    #[test]
    fn fluxir_equality() {
        let a = FluxIR::CheckExact { slot: 0, value: 42 };
        let b = FluxIR::CheckExact { slot: 0, value: 42 };
        assert_eq!(a, b);
    }

    #[test]
    fn halt_reason_variants() {
        let pass = HaltReason::Pass;
        let violation = HaltReason::Violation { slot: 3 };
        assert_ne!(pass, violation);
    }
}
