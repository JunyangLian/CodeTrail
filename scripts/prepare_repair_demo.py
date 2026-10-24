"""Create a fresh demo from the frozen task manifest without exposing reference edits."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / "benchmarks/repair_validation_tasks.json").read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in manifest["tasks"]}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=list(tasks))
    parser.add_argument("--output", help="New project-relative folder; must not already exist")
    args = parser.parse_args()
    output = (ROOT / (args.output or f"examples/repair-demo-{args.task}")).resolve()
    if not output.is_relative_to(ROOT):
        parser.error("output must stay inside this project")
    task = tasks[args.task]
    output.mkdir(parents=True, exist_ok=False)
    for name, content in task["files"].items():
        (output / name).write_text(content, encoding="utf-8", newline="\n")
    (output / "README.md").write_text(
        f"# {args.task}\n\n这是包含故意缺陷的修复演示，{task['expected_tests']} 个测试。\n\n"
        f"只允许修改：{', '.join(task['editable_files'])}。测试保持不变。\n\n"
        f"任务：{task['prompt']}\n\n"
        "独立验证：`python -B -m unittest discover -v`。初始失败是演示起点。\n",
        encoding="utf-8", newline="\n")
    print(output)
    print(f'python -X utf8 -m pico.web --cwd "{output.relative_to(ROOT).as_posix()}" --port 8767 --model-timeout 120 --verify-command "python -B -m unittest discover -v"')


if __name__ == "__main__":
    main()
