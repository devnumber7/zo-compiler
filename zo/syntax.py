from dataclasses import dataclass, field

from .source import Span


@dataclass
class Directive:
    name: str
    span: Span


@dataclass
class StageField:
    name: str
    type_name: str
    mutable: bool
    span: Span


@dataclass
class Stage:
    name: str
    directives: list[Directive]
    fields: list[StageField]
    span: Span
    allocation: str | None = None


@dataclass
class Expr:
    span: Span
    type_name: str | None = field(default=None, init=False)


@dataclass
class Integer(Expr):
    value: int


@dataclass
class Boolean(Expr):
    value: bool


@dataclass
class Name(Expr):
    name: str
    symbol_id: int | None = field(default=None, init=False)


@dataclass
class Member(Expr):
    base: Expr
    name: str


@dataclass
class Unary(Expr):
    operator: str
    operand: Expr


@dataclass
class Binary(Expr):
    left: Expr
    operator: str
    right: Expr


@dataclass
class Argument:
    name: str
    value: Expr
    span: Span


@dataclass
class Construct(Expr):
    name: str
    arguments: list[Argument]


@dataclass
class Statement:
    span: Span


@dataclass
class Declare(Statement):
    name: str
    mutable: bool
    annotation: str | None
    value: Expr
    symbol_id: int | None = field(default=None, init=False)


@dataclass
class Assign(Statement):
    target: Expr
    value: Expr


@dataclass
class Print(Statement):
    value: Expr


@dataclass
class Block(Statement):
    statements: list[Statement]


@dataclass
class Program:
    stages: list[Stage]
    main: Block
