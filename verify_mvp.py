"""Run the evidence-backed MVP contract suite.

Unlike the former scripted demo, this command does not assign imaginary QA
scores or print PASS unconditionally. Any unmet assertion returns a non-zero
process exit code.
"""

import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    print("AI-SVWF MVP 验证：接口契约、SQLite、状态机、QA 门禁、修复版本、FFmpeg 拼接")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=str(project_dir),
        check=False,
    )
    if result.returncode == 0:
        print("验证通过：所有结论均来自实际接口响应与文件/数据库断言。")
    else:
        print("验证失败：请根据 pytest 输出修复，不生成虚假通过结论。")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
