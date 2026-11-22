from dataclasses import dataclass

from .source import CompileError, Span


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    span: Span


KEYWORDS = {"stage", "func", "let", "var", "print", "true", "false"}


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    index, line, column = 0, 1, 1
    while index < len(source):
        char = source[index]
        if char in " \t\r":
            index += 1
            column += 1
            continue
        if source.startswith("//", index):
            while index < len(source) and source[index] != "\n":
                index += 1
                column += 1
            continue
        if char == "\n":
            tokens.append(Token("newline", char, Span(line, column)))
            index += 1
            line += 1
            column = 1
            continue
        start = index
        if char.isascii() and (char.isalpha() or char == "_"):
            index += 1
            while index < len(source) and source[index].isascii() and (
                source[index].isalnum() or source[index] == "_"
            ):
                index += 1
            text = source[start:index]
            kind = text if text in KEYWORDS else "identifier"
        elif char in "0123456789":
            index += 1
            while index < len(source) and source[index] in "0123456789":
                index += 1
            text, kind = source[start:index], "integer"
        elif char in "@{}():,.=+-*;":
            index += 1
            text = kind = char
        else:
            raise CompileError("E001", f"unexpected character {char!r}", Span(line, column))
        width = index - start
        tokens.append(Token(kind, text, Span(line, column, width)))
        column += width
    tokens.append(Token("eof", "", Span(line, column)))
    return tokens
