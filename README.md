# Zo stage compiler

A small executable experiment for Zo's **stage + directives** design. It reads
`.zo` programs, validates their directives and types, emits C, and uses Clang to
build native executables. The compiler is Python with no third-party runtime
dependencies.

## Try it

Requires Python 3.11+ and Clang (or GCC with the overflow builtins). From this folder:

```sh
python3 -m zo check examples/allocation.zo
python3 -m zo run examples/allocation.zo
python3 -m zo run examples/allocation.zo --trace-allocations
python3 -m zo build examples/allocation.zo -o build/demo
./build/demo
python3 -m zo emit-c examples/allocation.zo
```

The example prints:

```text
30
true
60
false
```

`build` also saves `build/demo.c` so you can inspect the layout, `malloc`, field
accesses, and `free` calls. `run` uses temporary build files. `emit-c` writes to
stdout, or accepts `-o build/demo.c`. `--cc /path/to/clang` selects a compiler;
the `CC` environment variable also accepts a compiler executable path.

Optionally install the command in a virtual environment with `python3 -m pip install -e .`;
then `zo run examples/allocation.zo` is equivalent to `python3 -m zo run ...`.

## A minimal program

```zo
@stack stage Point {
    let x: Int
    let y: Int
}

@heap stage Counter {
    var value: Int
}

func main() {
    let point = Point(x: 10, y: 20)
    var counter = Counter(value: 0)
    counter.value = point.x + point.y
    print(counter.value)
}
```

| Source concept | Implementation |
| --- | --- |
| `stage` | A C struct with fields laid out by the target C ABI |
| `@stack` | An inline C local with automatic storage duration |
| `@heap` | A pointer to storage obtained using `malloc` |
| End of a lexical block | `free` for its heap instances, in reverse construction order |
| Field access | `.` for inline instances; `->` for heap instances |
| `Int` | Signed 64-bit integer, with overflow checks for arithmetic |
| `Bool` | `true` or `false`, represented by C `bool` |

`@stack` describes storage semantics. Optimizing compilers may keep a value in
registers or eliminate it; this language contract does not require a physical
stack slot. Native builds currently use `-O0` to make generated behavior easy to inspect.

## Prototype language contract

- Each stage has exactly one allocation directive: `@stack` or `@heap`. There is
  no default. Directives may be on the same line or on preceding lines.
- Directives are collected before their effects are resolved. `@stack @heap`
  and `@heap @stack` produce the same conflict; later directives cannot override
  earlier ones. Repeated and unknown directives are errors. The two implemented
  directives are exclusive, so a useful combination of independent directives
  remains a future experiment.
- A stage has at least one field. Every field uses `let` or `var` and has type
  `Int` or `Bool`. Constructors supply every field exactly once with named
  arguments, in any order. There are no default field values.
- Programs have one `func main()` with no parameters or explicit return.
  Stages are declared at the top level and may appear before or after `main`.
- Local declarations use `let` or `var`, with an optional type annotation.
  Changing a field requires **both** a `var` binding and a `var` field. This
  rule applies equally to stack and heap instances; `let` makes either read-only.
- Stage instances must be constructed directly into a local variable. Stage
  copying, reassignment, moving, passing, and returning are unsupported. Primitive
  field values can be copied into other locals and constructor fields.
- Blocks establish lexical scopes. An inner scope may shadow an outer name;
  references outside a binding's scope and duplicate names in one scope are errors.
- Expressions support integer and boolean literals, variables, fields,
  parentheses, unary `-`, and integer `+`, `-`, `*`. Multiplication binds more
  tightly than addition/subtraction; binary operators associate left to right.
  Arithmetic overflow terminates with a source-location runtime error.
- `print(expression)` prints one integer or boolean followed by a newline.
- Newlines or semicolons separate statements and fields. The final separator
  before `}` is optional. Parenthesized expressions and constructor arguments
  can span lines. Constructor arguments allow a trailing comma. `//` starts a comment.
- Identifiers are ASCII letters, digits, and underscores, beginning with a
  letter or underscore. `stage`, `func`, `let`, `var`, `print`, `true`, and `false`
  are reserved. Stage names cannot redefine `Int` or `Bool`.

This is an **allocation prototype**. The original Zo proposal associates `@heap`
with reference counting; this experiment instead gives each instance a single
scope-bound owner. Cleanup runs on normal scope exit. Allocation failure and
integer overflow terminate the process without unwinding existing scopes.
It does not implement the proposal's full ownership or heap lifetime model.

`@copy`, access control, guests, inheritance, nested stages, strings, nullable
types, general functions, control flow, and concurrency are not implemented.
Unsupported directives are rejected rather than ignored.

## Diagnostics

```sh
python3 -m zo check examples/invalid/conflicting_directives.zo
```

```text
examples/invalid/conflicting_directives.zo:2:7: error[E212]: @stack and @heap are mutually exclusive
  stage Impossible {
        ^^^^^^^^^^
```

The `examples/invalid` folder also demonstrates immutable-field mutation and
stage aliasing errors. Invalid programs are rejected before invoking the C compiler.
Native programs send optional heap traces to stderr, keeping stdout for program output.

## Compiler structure

```text
Zo source → lexer → parser / AST → semantic checks → C generation → Clang → executable
```

| File | Responsibility |
| --- | --- |
| `zo/lexer.py` | Tokens and source locations |
| `zo/syntax.py`, `zo/parser.py` | Syntax tree and parsing |
| `zo/semantics.py` | Directive rules, scopes, types, construction, mutability |
| `zo/codegen.py` | Layout, allocation, checked arithmetic, scope cleanup |
| `zo/cli.py` | `check`, `emit-c`, `build`, `run` |

To add a directive, define its validation and resolved effect in `semantics.py`,
implement any resulting behavior, and add positive and negative examples. A
directive should only be accepted once its promised behavior is enforced.

## Verify

```sh
python3 -m unittest discover -v
```

Tests cover invalid programs, directive conflicts in either order, field and
binding mutability, lexical scope, arithmetic limits, native execution, CLI
commands, and paths with spaces. An allocator probe checks actual `malloc`/`free`
calls, including reverse cleanup order and zero live allocations after a successful
program. Another probe forces allocation failure to verify the null check.
Native tests are skipped if no C compiler is installed.

For an additional memory check with a sanitizer-capable Clang:

```sh
python3 -m zo emit-c examples/allocation.zo -o build/allocation.c
clang -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined build/allocation.c -o build/allocation-sanitized
./build/allocation-sanitized
```
