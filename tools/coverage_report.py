import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "coverage.json"
TEST_MAPPING = ROOT / "tests" / "module_tests.json"

def get_functions(source_file):
    tree = ast.parse(source_file.read_text(encoding="utf-8"))
    functions = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({
                "name": node.name,
                "start": node.lineno,
                "end": node.end_lineno,
            })

    return sorted(functions, key=lambda x: x["start"])


def analyze_module(name, data):
    source = ROOT / name

    executed = set(data["executed_lines"])
    missing = set(data["missing_lines"])
    executable = executed | missing

    functions = []
    for fn in get_functions(source):
        lines = {
            line for line in executable
            if fn["start"] <= line <= fn["end"]
        }
        covered = len(lines & executed)
        total = len(lines)

        functions.append({
            "name": fn["name"],
            "covered": covered,
            "total": total,
            "coverage": round(100 * covered / total, 2)
            if total else None,
        })

    summary = data["summary"]

    return {
        "module": name,
        "tested": len(executed) > 0,
        "coverage": round(summary["percent_covered"], 2),
        "functions": functions,
    }


def main():
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    modules = [
        analyze_module(name, info)
        for name, info in sorted(data["files"].items())
    ]

    mapping = json.loads(
        TEST_MAPPING.read_text(encoding="utf-8")
    )

    for module in modules:
        name = module["module"]
        test_path = mapping.get(name)

        module["test_file"] = test_path
        module["has_unit_tests"] = bool(
            test_path
            and (ROOT / test_path).is_file()
        )

    tested_count = sum(
        module["has_unit_tests"] for module in modules
    )

    print("\n=== MODULE TEST COVERAGE ===")

    for module in modules:
        status = (
            "TESTED" if module["has_unit_tests"]
            else "NOT TESTED"
        )
        print(f"{module['module']}: {status}")

    print(
        f"Module Test Coverage: "
        f"{tested_count}/{len(modules)} "
        f"({100 * tested_count / len(modules):.2f}%)"
    )

    covered_modules = sum(m["tested"] for m in modules)
    total_modules = len(modules)

    print("=== MODULE COVERAGE ===")
    for m in modules:
        print(
            f"{m['module']}: "
            f"{'EXECUTED' if m['tested'] else 'NOT EXECUTED'} "
            f"({m['coverage']}%)"
        )

    print(
        f"\nModule Coverage: "
        f"{covered_modules}/{total_modules} "
        f"({100 * covered_modules / total_modules:.2f}%)"
    )

    print(
        f"Code Coverage: "
        f"{data['totals']['percent_covered']:.2f}%"
    )

    print("\n=== FUNCTION COVERAGE ===")
    for m in modules:
        print(f"\n{m['module']}")
        for fn in m["functions"]:
            value = (
                f"{fn['coverage']}%"
                if fn["coverage"] is not None else "N/A"
            )
            print(
                f"  {fn['name']}: "
                f"{fn['covered']}/{fn['total']} "
                f"({value})"
            )

    output = ROOT / "reports" / "module_coverage.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(modules, indent=2),
        encoding="utf-8",
    )
    print(f"\nReport saved: {output}")


if __name__ == "__main__":
    main()
