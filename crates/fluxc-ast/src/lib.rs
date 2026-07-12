//! fluxc-ast — Universal AST types for the FLUX compiler.

use thiserror::Error;
use uuid::Uuid;

/// AST error type.
#[derive(Error, Debug)]
pub enum AstError {
    #[error("invalid AST node: {msg}")]
    InvalidNode { msg: String },
}

/// Unique identifier for an AST node.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct NodeId(Uuid);

impl NodeId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
}

impl Default for NodeId {
    fn default() -> Self {
        Self::new()
    }
}

/// A typed slot reference in the AST.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SlotRef {
    pub slot: u8,
    pub name: String,
}

/// A top-level constraint declaration in the AST.
#[derive(Debug, Clone, PartialEq)]
pub struct ConstraintDecl {
    pub id: NodeId,
    pub name: String,
    pub kind: ConstraintKind,
}

/// The kind of a constraint.
#[derive(Debug, Clone, PartialEq)]
pub enum ConstraintKind {
    Range { lo: i64, hi: i64 },
    Domain { mask: u64 },
    Exact { value: i64 },
    And(Box<ConstraintKind>, Box<ConstraintKind>),
    Or(Box<ConstraintKind>, Box<ConstraintKind>),
    Not(Box<ConstraintKind>),
}

/// A complete FLUX program AST.
#[derive(Debug, Clone, PartialEq)]
pub struct Program {
    pub id: NodeId,
    pub constraints: Vec<ConstraintDecl>,
}

impl Program {
    pub fn new() -> Self {
        Self {
            id: NodeId::new(),
            constraints: Vec::new(),
        }
    }
}

impl Default for Program {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn node_id_is_unique() {
        let a = NodeId::new();
        let b = NodeId::new();
        assert_ne!(a, b);
    }

    #[test]
    fn empty_program_has_no_constraints() {
        let prog = Program::new();
        assert!(prog.constraints.is_empty());
    }

    #[test]
    fn range_constraint_equality() {
        let c1 = ConstraintKind::Range { lo: 0, hi: 100 };
        let c2 = ConstraintKind::Range { lo: 0, hi: 100 };
        assert_eq!(c1, c2);
    }

    #[test]
    fn composite_constraint_and() {
        let left = ConstraintKind::Exact { value: 42 };
        let right = ConstraintKind::Range { lo: 0, hi: 100 };
        let combined = ConstraintKind::And(Box::new(left), Box::new(right));
        if let ConstraintKind::And(l, r) = combined {
            assert_eq!(*l, ConstraintKind::Exact { value: 42 });
            assert_eq!(*r, ConstraintKind::Range { lo: 0, hi: 100 });
        } else {
            panic!("expected And constraint");
        }
    }

    #[test]
    fn slot_ref_equality() {
        let s1 = SlotRef { slot: 1, name: "temp".into() };
        let s2 = SlotRef { slot: 1, name: "temp".into() };
        assert_eq!(s1, s2);
    }
}
