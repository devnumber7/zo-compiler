import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .codegen import generate_c
from .parser import parse
from .semantics import analyze
from .source import CompileError, Span


def compile_source(source: str, path: str = "<input>", trace: bool = False) -> str:
    try:
        return generate_c(analyze(parse(source)), path, trace)
    except RecursionError:
        raise CompileError("E108", "source nesting exceeds the prototype's limit", Span(1, 1)) from None


def native_build(c_path: Path, output: Path, cc: str | None) -> int:
    compiler = cc or os.environ.get("CC") or shutil.which("clang") or shutil.which("cc")
    if not compiler:
        print("zo: error: no C compiler found; install Clang or pass --cc PATH", file=sys.stderr)
        return 1
    result = subprocess.run([
        compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O0",
        str(c_path.resolve()), "-o", str(output.resolve()),
    ], check=False)
    if result.returncode:
        print(f"zo: C compilation failed; generated source is at {c_path}", file=sys.stderr)
    return result.returncode


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile Zo stages and allocation directives to native programs.")
    parser.add_argument("--version", action="version", version="zo 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [
        ("check", "parse and validate a program"),
        ("emit-c", "emit readable C"),
        ("build", "build a native executable and retain its C source"),
        ("run", "compile and run in a temporary directory"),
    ]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("source", type=Path)
        if name in {"emit-c", "build"}:
            command.add_argument("-o", "--output", type=Path)
        if name != "check":
            command.add_argument("--trace-allocations", action="store_true", help="log heap allocation and release to stderr")
        if name in {"build", "run"}:
            command.add_argument("--cc", help="C compiler executable (defaults to CC, clang, or cc)")
    return parser


def write_c(path: Path, code: str, source_path: Path) -> None:
    if path.resolve() == source_path.resolve():
        raise ValueError("output would overwrite the Zo source")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    source = ""
    try:
        source = args.source.read_text(encoding="utf-8")
        code = compile_source(source, str(args.source), getattr(args, "trace_allocations", False))
        if args.command == "check":
            print(f"OK: {args.source}")
            return 0
        if args.command == "emit-c":
            if args.output:
                write_c(args.output, code, args.source)
            else:
                sys.stdout.write(code)
            return 0
        if args.command == "build":
            output = args.output or Path("build") / args.source.stem
            if output.resolve() == args.source.resolve():
                raise ValueError("output would overwrite the Zo source")
            c_path = Path(str(output) + ".c")
            write_c(c_path, code, args.source)
            status = native_build(c_path, output, args.cc)
            if status == 0:
                print(f"Built {output} (C source: {c_path})")
            return status
        with tempfile.TemporaryDirectory(prefix="zo-run-") as directory:
            c_path = Path(directory) / "program.c"
            output = Path(directory) / "program"
            write_c(c_path, code, args.source)
            status = native_build(c_path, output, args.cc)
            if status:
                return status
            result = subprocess.run([str(output)], check=False)
            return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except CompileError as error:
        print(error.render(source, str(args.source)), file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as error:
        print(f"zo: error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
