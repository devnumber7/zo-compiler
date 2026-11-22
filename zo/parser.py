from .lexer import Token, lex
from .source import CompileError
from .syntax import (
    Argument, Assign, Binary, Block, Boolean, Construct, Declare, Directive,
    Expr, Integer, Member, Name, Print, Program, Stage, StageField, Unary,
)


class Parser:
    def __init__(self, source: str):
        self.tokens = lex(source)
        self.index = 0
        self.parentheses = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def take(self) -> Token:
        token = self.current
        if token.kind != "eof":
            self.index += 1
        return token

    def accept(self, kind: str) -> Token | None:
        return self.take() if self.current.kind == kind else None

    def expect(self, kind: str) -> Token:
        if self.current.kind != kind:
            found = self.current.text or "end of file"
            raise CompileError("E100", f"expected {kind!r}, found {found!r}", self.current.span)
        return self.take()

    def newlines(self) -> None:
        while self.accept("newline"):
            pass

    def separators(self) -> None:
        while self.current.kind in {"newline", ";"}:
            self.take()

    def finish_statement(self) -> None:
        if self.current.kind not in {"newline", ";", "}"}:
            raise CompileError("E101", "expected a newline, ';', or '}' after statement", self.current.span)
        self.separators()

    def parse(self) -> Program:
        stages: list[Stage] = []
        main: Block | None = None
        self.separators()
        while self.current.kind != "eof":
            directives: list[Directive] = []
            while self.accept("@"):
                token = self.expect("identifier")
                directives.append(Directive(token.text, token.span))
                self.newlines()
            if self.accept("stage"):
                stages.append(self.stage(directives))
            elif self.current.kind == "func" and not directives:
                self.take()
                name = self.expect("identifier")
                if name.text != "main" or main is not None:
                    raise CompileError("E102", "the prototype supports exactly one function: main", name.span)
                self.expect("(")
                self.newlines()
                self.expect(")")
                self.newlines()
                main = self.block()
            else:
                raise CompileError("E103", "expected a stage declaration or func main()", self.current.span)
            self.separators()
        if main is None:
            raise CompileError("E104", "program requires func main()", self.current.span)
        return Program(stages, main)

    def stage(self, directives: list[Directive]) -> Stage:
        name = self.expect("identifier")
        self.newlines()
        self.expect("{")
        self.separators()
        fields: list[StageField] = []
        while self.current.kind != "}":
            if self.current.kind not in {"let", "var"}:
                raise CompileError("E105", "expected a field declared with let or var, or '}'", self.current.span)
            mutable = self.take().kind == "var"
            field = self.expect("identifier")
            self.expect(":")
            type_name = self.expect("identifier")
            fields.append(StageField(field.text, type_name.text, mutable, field.span))
            self.finish_statement()
        self.expect("}")
        return Stage(name.text, directives, fields, name.span)

    def block(self) -> Block:
        start = self.expect("{")
        self.separators()
        statements = []
        while self.current.kind != "}":
            token = self.current
            if token.kind in {"let", "var"}:
                mutable = self.take().kind == "var"
                name = self.expect("identifier")
                annotation = self.expect("identifier").text if self.accept(":") else None
                self.expect("=")
                statements.append(Declare(name.span, name.text, mutable, annotation, self.expression()))
            elif self.accept("print"):
                self.expect("(")
                self.parentheses += 1
                value = self.expression()
                self.expect(")")
                self.parentheses -= 1
                statements.append(Print(token.span, value))
            elif token.kind == "{":
                statements.append(self.block())
            elif token.kind == "identifier":
                target = self.expression()
                self.expect("=")
                statements.append(Assign(token.span, target, self.expression()))
            else:
                raise CompileError("E106", "expected a variable, assignment, print, block, or '}'", token.span)
            self.finish_statement()
        self.expect("}")
        return Block(start.span, statements)

    def expression(self, minimum: int = 0) -> Expr:
        if self.parentheses:
            self.newlines()
        token = self.take()
        if token.kind == "integer":
            # Bound conversion before Python's arbitrary precision integer parser.
            digits = token.text.lstrip("0") or "0"
            if len(digits) > 19:
                raise CompileError("E200", "Int literal is outside the signed 64-bit range", token.span)
            result: Expr = Integer(token.span, int(digits))
        elif token.kind in {"true", "false"}:
            result = Boolean(token.span, token.kind == "true")
        elif token.kind == "-":
            result = Unary(token.span, "-", self.expression(30))
        elif token.kind == "(":
            self.parentheses += 1
            result = self.expression()
            self.expect(")")
            self.parentheses -= 1
        elif token.kind == "identifier":
            if self.accept("("):
                self.parentheses += 1
                self.newlines()
                arguments = []
                while self.current.kind != ")":
                    name = self.expect("identifier")
                    self.expect(":")
                    arguments.append(Argument(name.text, self.expression(), name.span))
                    if not self.accept(","):
                        break
                    self.newlines()
                self.expect(")")
                self.parentheses -= 1
                result = Construct(token.span, token.text, arguments)
            else:
                result = Name(token.span, token.text)
        else:
            raise CompileError("E107", "expected an integer, boolean, variable, or constructor", token.span)
        while True:
            if self.parentheses:
                self.newlines()
            if self.accept("."):
                name = self.expect("identifier")
                result = Member(name.span, result, name.text)
                continue
            precedence = {"+": 10, "-": 10, "*": 20}.get(self.current.kind, -1)
            if precedence < minimum:
                break
            operator = self.take()
            result = Binary(operator.span, result, operator.kind, self.expression(precedence + 1))
        return result


def parse(source: str) -> Program:
    return Parser(source).parse()
