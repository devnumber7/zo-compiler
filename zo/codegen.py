from .semantics import CheckedProgram, INT_MAX
from .syntax import (
    Assign, Binary, Block, Boolean, Construct, Declare, Expr, Integer,
    Member, Name, Print, Unary,
)


def c_string(text: str) -> str:
    """Encode arbitrary paths/names without allowing them to become C syntax."""
    return '"' + "".join(
        chr(byte) if 32 <= byte < 127 and byte not in (34, 92)
        else f"\\{byte:03o}"
        for byte in text.encode("utf-8")
    ) + '"'


class Generator:
    def __init__(self, checked: CheckedProgram, source_path: str, trace: bool):
        self.checked = checked
        self.source_path = source_path
        self.trace = trace
        self.stage_ids = {name: index for index, name in enumerate(checked.stages)}
        self.lines: list[str] = []
        self.indent = 0
        self.temporary = 0
        self.operations: set[str] = set()
        self.uses_heap = False

    def emit(self, text: str) -> None:
        self.lines.append("    " * self.indent + text)

    def temp(self, expression: str) -> str:
        name = f"zo_t_{self.temporary}"
        self.temporary += 1
        self.emit(f"int64_t {name} = {expression};")
        return name

    def field_name(self, stage_name: str, member: str) -> str:
        fields = self.checked.stages[stage_name].fields
        return f"zo_f_{next(i for i, field in enumerate(fields) if field.name == member)}"

    def expression(self, expr: Expr) -> str:
        if isinstance(expr, Integer):
            return f"INT64_C({expr.value})"
        if isinstance(expr, Boolean):
            return "true" if expr.value else "false"
        if isinstance(expr, Name):
            return f"zo_v_{expr.symbol_id}"
        if isinstance(expr, Member):
            stage = self.checked.stages[expr.base.type_name]
            access = "->" if stage.allocation == "heap" else "."
            return self.expression(expr.base) + access + self.field_name(stage.name, expr.name)
        if isinstance(expr, Unary):
            if isinstance(expr.operand, Integer) and expr.operand.value == INT_MAX + 1:
                return "INT64_MIN"
            self.operations.add("sub")
            value = self.expression(expr.operand)
            return self.temp(f"zo_sub(INT64_C(0), {value}, {expr.span.line}, {expr.span.column})")
        if isinstance(expr, Binary):
            # Materialize arithmetic in source order; C argument evaluation order
            # must not choose which failing arithmetic expression executes first.
            left = self.expression(expr.left)
            right = self.expression(expr.right)
            operation = {"+": "add", "-": "sub", "*": "mul"}[expr.operator]
            self.operations.add(operation)
            return self.temp(f"zo_{operation}({left}, {right}, {expr.span.line}, {expr.span.column})")
        raise AssertionError(f"unhandled expression: {expr}")

    def trace_event(self, event: str, stage_name: str, variable: str) -> None:
        if self.trace:
            message = c_string(f"zo: heap {event} {stage_name} ({variable})\n")
            self.emit(f"fputs({message}, stderr);")

    def block(self, block: Block, main: bool = False) -> None:
        self.emit("int main(void) {" if main else "{")
        self.indent += 1
        cleanup: list[tuple[str, str, str]] = []
        for statement in block.statements:
            if isinstance(statement, Block):
                self.block(statement)
            elif isinstance(statement, Declare):
                name = f"zo_v_{statement.symbol_id}"
                if isinstance(statement.value, Construct):
                    constructor = statement.value
                    stage = self.checked.stages[constructor.name]
                    c_type = f"struct zo_s_{self.stage_ids[stage.name]}"
                    values = [
                        (self.field_name(stage.name, argument.name), self.expression(argument.value))
                        for argument in constructor.arguments
                    ]
                    if stage.allocation == "stack":
                        fields = ", ".join(f".{field} = {value}" for field, value in values)
                        self.emit(f"{c_type} {name} = {{ {fields} }};")
                    else:
                        self.uses_heap = True
                        self.emit(f"{c_type} *{name} = malloc(sizeof *{name});")
                        self.emit(f"if ({name} == NULL) zo_fail(\"heap allocation failed\", {statement.span.line}, {statement.span.column});")
                        for field, value in values:
                            self.emit(f"{name}->{field} = {value};")
                        self.trace_event("alloc", stage.name, statement.name)
                        cleanup.append((name, stage.name, statement.name))
                else:
                    value = self.expression(statement.value)
                    c_type = "int64_t" if statement.value.type_name == "Int" else "bool"
                    self.emit(f"{c_type} {name} = {value};")
                self.emit(f"(void){name};")
            elif isinstance(statement, Assign):
                value = self.expression(statement.value)
                self.emit(f"{self.expression(statement.target)} = {value};")
            elif isinstance(statement, Print):
                value = self.expression(statement.value)
                if statement.value.type_name == "Int":
                    self.emit(f'printf("%" PRId64 "\\n", {value});')
                else:
                    self.emit(f'puts({value} ? "true" : "false");')
            else:
                raise AssertionError(f"unhandled statement: {statement}")
        for name, stage_name, variable in reversed(cleanup):
            self.emit(f"free({name});")
            self.trace_event("free", stage_name, variable)
        if main:
            self.emit("return 0;")
        self.indent -= 1
        self.emit("}")

    def generate(self) -> str:
        self.block(self.checked.program.main, main=True)
        header = [
            "/* Generated by Zo's stage prototype. */",
            "#include <stdbool.h>", "#include <stdint.h>", "#include <inttypes.h>",
            "#include <stdio.h>", "#include <stdlib.h>", "",
        ]
        for name, stage in self.checked.stages.items():
            header.append(f"struct zo_s_{self.stage_ids[name]} {{")
            for index, field in enumerate(stage.fields):
                c_type = "int64_t" if field.type_name == "Int" else "bool"
                header.append(f"    {c_type} zo_f_{index};")
            header.extend(["};", ""])
        if self.uses_heap or self.operations:
            header.extend([
                "static void zo_fail(const char *message, int line, int column) {",
                f'    fprintf(stderr, "%s:%d:%d: runtime error: %s\\n", {c_string(self.source_path)}, line, column, message);',
                "    exit(EXIT_FAILURE);", "}", "",
            ])
        for operation in sorted(self.operations):
            header.extend([
                f"static int64_t zo_{operation}(int64_t a, int64_t b, int line, int column) {{",
                "    int64_t result;",
                f'    if (__builtin_{operation}_overflow(a, b, &result)) zo_fail("Int overflow", line, column);',
                "    return result;", "}", "",
            ])
        return "\n".join(header + self.lines) + "\n"


def generate_c(checked: CheckedProgram, source_path: str = "<input>", trace: bool = False) -> str:
    return Generator(checked, source_path, trace).generate()
