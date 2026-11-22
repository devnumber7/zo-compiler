from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    line: int
    column: int
    width: int = 1


class CompileError(Exception):
    def __init__(self, code: str, message: str, span: Span):
        super().__init__(message)
        self.code = code
        self.message = message
        self.span = span

    def render(self, source: str, path: str) -> str:
        lines = source.splitlines()
        line = lines[self.span.line - 1] if self.span.line <= len(lines) else ""
        prefix = line[:self.span.column - 1].expandtabs(4)
        width = max(1, min(self.span.width, len(line) - self.span.column + 1))
        return (
            f"{path}:{self.span.line}:{self.span.column}: error[{self.code}]: {self.message}\n"
            f"  {line.expandtabs(4)}\n  {' ' * len(prefix)}{'^' * width}"
        )
