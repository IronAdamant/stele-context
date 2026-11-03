//! Shared node and token kinds. Languages may use the ranges above 300.

pub const ERROR: u16 = 1;
pub const WHITESPACE: u16 = 2;
pub const COMMENT: u16 = 3;
pub const NEWLINE: u16 = 4;
pub const IDENT: u16 = 5;
pub const NUMBER: u16 = 6;
pub const STRING: u16 = 7;
pub const OP: u16 = 8;
pub const INDENT: u16 = 9;
pub const DEDENT: u16 = 10;
pub const EOF: u16 = 11;

pub const FILE: u16 = 100;
pub const FUNC: u16 = 101;
pub const CLASS: u16 = 102;
pub const SUITE: u16 = 103;
pub const IMPORT: u16 = 104;
pub const PARAMS: u16 = 105;
pub const DECORATOR: u16 = 106;
pub const RETURNS: u16 = 107;
pub const CALL: u16 = 108;
pub const NAME: u16 = 109;
pub const JSON_VALUE: u16 = 200;
pub const JSON_OBJECT: u16 = 201;
pub const JSON_ARRAY: u16 = 202;
