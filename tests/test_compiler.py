from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from zo.cli import compile_source
from zo.source import CompileError


ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("clang") or shutil.which("cc")
STAGE = "@heap stage Box { var value: Int; let ready: Bool }\n"


def program(body: str, declarations: str = STAGE) -> str:
    return declarations + "func main() {\n" + body + "\n}\n"


class ValidationTests(unittest.TestCase):
    def test_directive_order_cannot_override_allocation(self):
        errors = []
        for directives in ("@stack @heap", "@heap @stack"):
            with self.assertRaises(CompileError) as caught:
                compile_source(program("", f"{directives} stage Box {{ let value: Int }}\n"))
            errors.append((caught.exception.code, caught.exception.message))
        self.assertEqual(errors, [("E212", "@stack and @heap are mutually exclusive")] * 2)

    def test_directive_layout_does_not_change_code(self):
        first = "@stack stage Box { let value: Int }\nfunc main() {}"
        second = "@stack\n// allocation directive\nstage Box { let value: Int }\nfunc main() {}"
        self.assertEqual(compile_source(first), compile_source(second))

    def test_rejects_invalid_programs(self):
        cases = [
            ("missing allocation", "stage Box { let value: Int }\nfunc main() {}", "E213"),
            ("duplicate directive", "@heap @heap stage Box { let value: Int }\nfunc main() {}", "E210"),
            ("unknown directive", "@heep stage Box { let value: Int }\nfunc main() {}", "E211"),
            ("deferred directive", "@stack @copy stage Box { let value: Int }\nfunc main() {}", "E211"),
            ("no main", "@stack stage Box { let value: Int }", "E104"),
            ("two mains", "func main() {}\nfunc main() {}", "E102"),
            ("other function", "func other() {}", "E102"),
            ("duplicate stage", STAGE + STAGE + "func main() {}", "E201"),
            ("builtin type name", "@stack stage Int { let value: Int }\nfunc main() {}", "E201"),
            ("empty stage", "@stack stage Box {}\nfunc main() {}", "E202"),
            ("duplicate field", "@stack stage Box { let x: Int; var x: Bool }\nfunc main() {}", "E203"),
            ("resource field", "@heap stage Box { let x: String }\nfunc main() {}", "E204"),
            ("missing field", program("let b = Box(value: 1)"), "E228"),
            ("duplicate argument", program("let b = Box(value: 1, value: 2, ready: true)"), "E226"),
            ("unknown argument", program("let b = Box(typo: 1, ready: true)"), "E227"),
            ("wrong field type", program("let b = Box(value: true, ready: false)"), "E221"),
            ("unknown stage", program("let b = Unknown(value: 1)"), "E225"),
            ("positional constructor", program("let b = Box(1, true)"), "E100"),
            ("copy heap stage", program("let b = Box(value: 1, ready: true)\nlet alias = b"), "E232"),
            ("copy stack stage", program("let b = Box(value: 1, ready: true)\nlet alias = b", STAGE.replace("@heap", "@stack")), "E232"),
            ("stage reassignment", program("var b = Box(value: 1, ready: true)\nb = Box(value: 2, ready: true)"), "E235"),
            ("immutable field", program("var b = Box(value: 1, ready: true)\nb.ready = false"), "E233"),
            ("immutable binding", program("let b = Box(value: 1, ready: true)\nb.value = 2"), "E236"),
            ("immutable scalar", program("let n = 1\nn = 2"), "E236"),
            ("unknown member", program("let b = Box(value: 1, ready: true)\nprint(b.typo)"), "E224"),
            ("scalar member", program("let n = 1\nprint(n.value)"), "E222"),
            ("temporary stage member", program("print(Box(value: 1, ready: true).value)"), "E223"),
            ("unknown variable", program("print(missing)"), "E220"),
            ("use before declaration", program("print(n)\nlet n = 1"), "E220"),
            ("scope escape", program("{ let b = Box(value: 1, ready: true) }\nprint(b.value)"), "E220"),
            ("duplicate binding", program("let n = 1\nlet n = 2"), "E230"),
            ("wrong annotation", program("let n: Bool = 1"), "E221"),
            ("unknown annotation", program("let n: Nope = 1"), "E231"),
            ("boolean arithmetic", program("print(true + 1)"), "E221"),
            ("boolean negation", program("print(-true)"), "E221"),
            ("print stage", program("let b = Box(value: 1, ready: true)\nprint(b)"), "E237"),
            ("assignment mismatch", program("var n = 1\nn = false"), "E221"),
            ("nonassignable expression", program("var n = 1\nn + 1 = 2"), "E234"),
            ("large positive", program("print(9223372036854775808)"), "E200"),
            ("large negative", program("print(-9223372036854775809)"), "E200"),
            ("huge literal", program("print(" + "9" * 5000 + ")"), "E200"),
            ("unsupported character", program("print(4 / 2)"), "E001"),
            ("missing closing brace", "func main() {", "E106"),
            ("missing separator", program("let n = 1 print(n)"), "E101"),
        ]
        for label, source, code in cases:
            with self.subTest(label=label):
                with self.assertRaises(CompileError) as caught:
                    compile_source(source)
                self.assertEqual(caught.exception.code, code)

    def test_error_includes_source_location_and_line(self):
        source = program("var b = Box(value: 1, ready: true)\nb.ready = false")
        with self.assertRaises(CompileError) as caught:
            compile_source(source)
        self.assertEqual(caught.exception.span.line, 4)
        rendered = caught.exception.render(source, "example.zo")
        self.assertIn("example.zo:4:3: error[E233]", rendered)
        self.assertIn("b.ready = false", rendered)
        self.assertIn("^^^^^", rendered)

    def test_path_is_escaped_in_generated_c(self):
        code = compile_source(program("print(1 + 2)"), 'a"\\\n.zo')
        self.assertIn(r'a\042\134\012.zo', code)


@unittest.skipUnless(CC, "native integration tests require Clang or GCC")
class NativeTests(unittest.TestCase):
    def run_c(self, code: str, flags: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="zo-test-") as directory:
            source = Path(directory) / "program.c"
            binary = Path(directory) / "program"
            source.write_text(code, encoding="utf-8")
            build = subprocess.run(
                [CC, "-std=c11", "-Wall", "-Wextra", "-Werror", *flags, str(source), "-o", str(binary)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)

    def test_example_output_and_cleanup_trace(self):
        source = (ROOT / "examples/allocation.zo").read_text()
        result = self.run_c(compile_source(source, trace=True))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "30\ntrue\n60\nfalse\n")
        self.assertEqual(result.stderr.splitlines(), [
            "zo: heap alloc Counter (counter)", "zo: heap alloc Counter (temporary)",
            "zo: heap free Counter (temporary)", "zo: heap free Counter (counter)",
        ])

    def test_actual_allocator_calls_and_reverse_scope_cleanup(self):
        # Probe malloc/free themselves, independently of the compiler's trace text.
        probe = r'''
#include <stdlib.h>
#include <stdio.h>
static void *live[8];
static int count = 0, allocations = 0;
static void *probe_malloc(size_t size) {
    void *p = malloc(size);
    if (!p || count >= 8) abort();
    live[count++] = p;
    allocations++;
    return p;
}
static void probe_free(void *p) {
    if (count == 0 || live[count - 1] != p) abort();
    count--;
    free(p);
}
#define malloc probe_malloc
#define free probe_free
'''
        source = program("""
let a = Box(value: 1, ready: true)
{
    let b = Box(value: 2, ready: false)
    let c = Box(value: 3, ready: true)
    print(c.value)
}
let d = Box(value: 4, ready: true)
print(a.value + d.value)
""")
        code = compile_source(source).replace("int main(void)", "int zo_program(void)")
        footer = "int main(void) { int r = zo_program(); if (count != 0 || allocations != 4) abort(); return r; }"
        result = self.run_c(probe + code + footer)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "3\n5\n")

    def test_stack_program_does_not_call_allocator(self):
        source = program("var b = Box(value: 7, ready: true)\nb.value = b.value + 1\nprint(b.value)", STAGE.replace("@heap", "@stack"))
        code = compile_source(source)
        self.assertNotIn("malloc(", code)
        self.assertNotIn("free(", code)
        result = self.run_c(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "8\n")

    def test_allocation_failure_is_reported_before_dereference(self):
        probe = "#include <stdlib.h>\nstatic void *fail_alloc(size_t n) { (void)n; return NULL; }\n#define malloc fail_alloc\n"
        result = self.run_c(probe + compile_source(program("let b = Box(value: 1, ready: true)"), "fail.zo"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("fail.zo:3:5: runtime error: heap allocation failed", result.stderr)

    def test_arithmetic_scopes_multiline_and_c_identifier_hygiene(self):
        source = program("""
var value: Int = 8 - 3 - 1
print(value)
value = -value + 2 * (3 + 4)
print(value)
{
    let value = value + 1
    print(value)
}
print(value)
let malloc = Box(
    ready: true,
    value: (
        -9223372036854775808
        + 1
    ),
)
print(malloc.value)
print(9223372036854775807)
print(-9223372036854775808)
var int: Bool = true
int = false
print(int)
""")
        result = self.run_c(compile_source(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "4\n10\n11\n10\n-9223372036854775807\n9223372036854775807\n-9223372036854775808\nfalse\n")

    def test_checked_arithmetic_overflow(self):
        for expression in ["9223372036854775807 + 1", "-9223372036854775808 - 1", "9223372036854775807 * 2", "-(-9223372036854775808)"]:
            with self.subTest(expression=expression):
                result = self.run_c(compile_source(program(f"print({expression})"), "overflow.zo"))
                self.assertEqual(result.returncode, 1)
                self.assertIn("runtime error: Int overflow", result.stderr)

    def test_minimal_program_and_unused_locals_compile_cleanly(self):
        result = self.run_c(compile_source("func main() { let unused = 1 }"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


class CLITests(unittest.TestCase):
    def cli(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "zo", *arguments], cwd=ROOT, capture_output=True, text=True, timeout=30)

    def test_check_and_emit(self):
        self.assertEqual(self.cli("check", "examples/allocation.zo").returncode, 0)
        emitted = self.cli("emit-c", "examples/allocation.zo")
        self.assertEqual(emitted.returncode, 0, emitted.stderr)
        self.assertIn("malloc(sizeof", emitted.stdout)

    def test_invalid_source_has_no_traceback_or_generated_output(self):
        result = self.cli("emit-c", "examples/invalid/conflicting_directives.zo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error[E212]", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_file_and_compiler(self):
        self.assertEqual(self.cli("check", "missing.zo").returncode, 1)
        result = self.cli("run", "examples/allocation.zo", "--cc", "/nonexistent/zo-compiler")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_outputs_cannot_overwrite_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "demo.zo"
            source.write_text("func main() {}")
            for command in ("emit-c", "build"):
                result = self.cli(command, str(source), "-o", str(source))
                self.assertEqual(result.returncode, 1)
                self.assertIn("overwrite", result.stderr)
                self.assertEqual(source.read_text(), "func main() {}")

    @unittest.skipUnless(CC, "requires a C compiler")
    def test_build_and_run_in_paths_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="zo tests ") as directory:
            binary = Path(directory) / "output folder" / "demo"
            result = self.cli("build", "examples/allocation.zo", "-o", str(binary), "--cc", CC)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(Path(str(binary) + ".c").is_file())
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.stdout, "30\ntrue\n60\nfalse\n")
            result = self.cli("run", "examples/allocation.zo", "--cc", CC)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "30\ntrue\n60\nfalse\n")


if __name__ == "__main__":
    unittest.main()
