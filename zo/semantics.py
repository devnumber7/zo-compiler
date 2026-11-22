from dataclasses import dataclass

from .source import CompileError, Span
from .syntax import (
    Assign, Binary, Block, Boolean, Construct, Declare, Expr, Integer,
    Member, Name, Print, Program, Stage, Unary,
)


PRIMITIVES = {"Int", "Bool"}
INT_MAX = (1 << 63) - 1


@dataclass(frozen=True)
class DirectiveRule:
    allocation: str


# Resolve the complete directive set before applying its effects. No directive
# can override another by appearing later in the source.
DIRECTIVES = {
    "stack": DirectiveRule(allocation="stack"),
    "heap": DirectiveRule(allocation="heap"),
}
DEFERRED_DIRECTIVES = {
    "copy", "private", "final", "allow_inheritance", "own", "guest", "mut",
    "lives_with", "unique", "queue", "free", "shareable", "runtime",
}


@dataclass(frozen=True)
class Symbol:
    id: int
    name: str
    type_name: str
    mutable: bool
    span: Span


@dataclass
class CheckedProgram:
    program: Program
    stages: dict[str, Stage]
    symbols: dict[int, Symbol]


class Analyzer:
    def __init__(self, program: Program):
        self.program = program
        self.stages: dict[str, Stage] = {}
        self.symbols: dict[int, Symbol] = {}
        self.scopes: list[dict[str, Symbol]] = []

    def analyze(self) -> CheckedProgram:
        for stage in self.program.stages:
            if stage.name in self.stages or stage.name in PRIMITIVES:
                raise CompileError("E201", f"type '{stage.name}' is already defined", stage.span)
            self.validate_directives(stage)
            fields = set()
            if not stage.fields:
                raise CompileError("E202", "a stage must have at least one field", stage.span)
            for field in stage.fields:
                if field.name in fields:
                    raise CompileError("E203", f"duplicate field '{field.name}'", field.span)
                fields.add(field.name)
                if field.type_name not in PRIMITIVES:
                    raise CompileError("E204", "stage fields currently support only Int and Bool", field.span)
            self.stages[stage.name] = stage
        self.block(self.program.main)
        return CheckedProgram(self.program, self.stages, self.symbols)

    def validate_directives(self, stage: Stage) -> None:
        seen = set()
        for directive in stage.directives:
            if directive.name in seen:
                raise CompileError("E210", f"duplicate directive '@{directive.name}'", directive.span)
            seen.add(directive.name)
            if directive.name not in DIRECTIVES:
                message = (
                    f"directive '@{directive.name}' is not supported by this prototype"
                    if directive.name in DEFERRED_DIRECTIVES
                    else f"unknown directive '@{directive.name}'"
                )
                raise CompileError("E211", message, directive.span)
        allocations = {DIRECTIVES[name].allocation for name in seen}
        if len(allocations) > 1:
            raise CompileError("E212", "@stack and @heap are mutually exclusive", stage.span)
        if not allocations:
            raise CompileError("E213", "stage requires exactly one allocation directive: @stack or @heap", stage.span)
        stage.allocation = next(iter(allocations))

    def lookup(self, name: Name) -> Symbol:
        for scope in reversed(self.scopes):
            if name.name in scope:
                symbol = scope[name.name]
                name.symbol_id = symbol.id
                return symbol
        raise CompileError("E220", f"unknown variable '{name.name}'", name.span)

    def require_type(self, actual: str, expected: str, span: Span) -> None:
        if actual != expected:
            raise CompileError("E221", f"expected {expected}, found {actual}", span)

    def expression(self, expr: Expr) -> str:
        if isinstance(expr, Integer):
            if expr.value > INT_MAX:
                raise CompileError("E200", "Int literal is outside the signed 64-bit range", expr.span)
            result = "Int"
        elif isinstance(expr, Boolean):
            result = "Bool"
        elif isinstance(expr, Name):
            result = self.lookup(expr).type_name
        elif isinstance(expr, Member):
            base_type = self.expression(expr.base)
            if base_type not in self.stages:
                raise CompileError("E222", f"{base_type} has no fields", expr.span)
            if not isinstance(expr.base, Name):
                raise CompileError("E223", "construct a stage in a local variable before accessing its fields", expr.span)
            stage = self.stages[base_type]
            member = next((f for f in stage.fields if f.name == expr.name), None)
            if member is None:
                raise CompileError("E224", f"stage '{stage.name}' has no field '{expr.name}'", expr.span)
            result = member.type_name
        elif isinstance(expr, Unary):
            # Accept the minimum signed Int without first rejecting its positive magnitude.
            if isinstance(expr.operand, Integer) and expr.operand.value == INT_MAX + 1:
                expr.operand.type_name = "Int"
            else:
                self.require_type(self.expression(expr.operand), "Int", expr.span)
            result = "Int"
        elif isinstance(expr, Binary):
            self.require_type(self.expression(expr.left), "Int", expr.left.span)
            self.require_type(self.expression(expr.right), "Int", expr.right.span)
            result = "Int"
        elif isinstance(expr, Construct):
            if expr.name not in self.stages:
                raise CompileError("E225", f"unknown stage '{expr.name}'", expr.span)
            stage = self.stages[expr.name]
            fields = {field.name: field for field in stage.fields}
            supplied = set()
            for argument in expr.arguments:
                if argument.name in supplied:
                    raise CompileError("E226", f"duplicate argument '{argument.name}'", argument.span)
                supplied.add(argument.name)
                if argument.name not in fields:
                    raise CompileError("E227", f"stage '{stage.name}' has no field '{argument.name}'", argument.span)
                self.require_type(self.expression(argument.value), fields[argument.name].type_name, argument.span)
            missing = fields.keys() - supplied
            if missing:
                raise CompileError("E228", f"missing constructor fields: {', '.join(sorted(missing))}", expr.span)
            result = expr.name
        else:
            raise AssertionError(f"unhandled expression: {expr}")
        expr.type_name = result
        return result

    def block(self, block: Block) -> None:
        self.scopes.append({})
        for statement in block.statements:
            if isinstance(statement, Block):
                self.block(statement)
            elif isinstance(statement, Declare):
                if statement.name in self.scopes[-1]:
                    raise CompileError("E230", f"variable '{statement.name}' is already defined in this scope", statement.span)
                type_name = self.expression(statement.value)
                if statement.annotation:
                    if statement.annotation not in PRIMITIVES | self.stages.keys():
                        raise CompileError("E231", f"unknown type '{statement.annotation}'", statement.span)
                    self.require_type(type_name, statement.annotation, statement.span)
                if type_name in self.stages and not isinstance(statement.value, Construct):
                    raise CompileError("E232", "stage copying and moving are not supported; construct a fresh instance", statement.span)
                symbol = Symbol(len(self.symbols), statement.name, type_name, statement.mutable, statement.span)
                self.scopes[-1][statement.name] = symbol
                self.symbols[symbol.id] = symbol
                statement.symbol_id = symbol.id
            elif isinstance(statement, Assign):
                target_type = self.expression(statement.target)
                if isinstance(statement.target, Name):
                    symbol = self.symbols[statement.target.symbol_id]
                elif isinstance(statement.target, Member):
                    base = statement.target.base
                    assert isinstance(base, Name)
                    symbol = self.symbols[base.symbol_id]
                    stage = self.stages[symbol.type_name]
                    member = next(f for f in stage.fields if f.name == statement.target.name)
                    if not member.mutable:
                        raise CompileError("E233", f"field '{member.name}' is immutable; declare it with var to mutate it", statement.target.span)
                else:
                    raise CompileError("E234", "assignment requires a variable or a stage field", statement.target.span)
                if target_type in self.stages:
                    raise CompileError("E235", "stage reassignment is not supported", statement.span)
                if not symbol.mutable:
                    raise CompileError("E236", f"variable '{symbol.name}' is immutable; declare it with var to mutate it", statement.target.span)
                self.require_type(self.expression(statement.value), target_type, statement.value.span)
            elif isinstance(statement, Print):
                if self.expression(statement.value) not in PRIMITIVES:
                    raise CompileError("E237", "print accepts an Int or Bool; select a stage field", statement.span)
            else:
                raise AssertionError(f"unhandled statement: {statement}")
        self.scopes.pop()


def analyze(program: Program) -> CheckedProgram:
    return Analyzer(program).analyze()
