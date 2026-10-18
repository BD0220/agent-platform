"""
端到端测试：给平台一个真实任务，验证完整工作流。
"""
import os
import sys
import json
import time
import tempfile
import shutil

# 配置环境变量（从 .env 或系统环境变量读取，不硬编码）
from pathlib import Path
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
os.environ.setdefault("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
os.environ.setdefault("LLM_PROVIDER", "deepseek")

# 用临时目录隔离数据
test_data = tempfile.mkdtemp(prefix="e2e_data_")
test_deliveries = tempfile.mkdtemp(prefix="e2e_deliveries_")
os.environ["AGENT_DATA_DIR"] = test_data
os.environ["AGENT_DELIVERIES_DIR"] = test_deliveries

# 确保从 src 目录导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

print("=" * 60)
print("  多智能体平台 E2E 测试")
print("=" * 60)
print(f"数据目录: {test_data}")
print(f"交付目录: {test_deliveries}")
print()

# 简单任务：写一个判断回文字符串的函数
TASK = "写一个Python函数 is_palindrome(s)，判断输入字符串是否是回文（忽略大小写和非字母数字字符），并给出测试用例"

print(f">>> 任务: {TASK}")
print()

start = time.time()

try:
    from agent_platform.main import run_task
    result = run_task(TASK)
    elapsed = time.time() - start

    print()
    print("=" * 60)
    print(f"  完成！耗时 {elapsed:.1f}s")
    print("=" * 60)
    print()

    # 打印结果摘要
    for key, value in result.items():
        print(f"{'─' * 40}")
        print(f"【{key}】")
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        # 打印前500字
        if len(text) > 500:
            print(text[:500] + "...")
        else:
            print(text)
        print()

    # 检查交付目录
    print("=" * 60)
    print("  交付产物")
    print("=" * 60)
    for root, dirs, files in os.walk(test_deliveries):
        for f in files:
            fp = os.path.join(root, f)
            size = os.path.getsize(fp)
            print(f"  📄 {os.path.relpath(fp, test_deliveries)} ({size} bytes)")

    # 检查数据目录
    print()
    print("=" * 60)
    print("  数据目录结构")
    print("=" * 60)
    for root, dirs, files in os.walk(test_data):
        level = root.replace(test_data, "").count(os.sep)
        indent = "  " * level
        dirname = os.path.basename(root) or "data/"
        print(f"{indent}{dirname}/")
        for f in files:
            fp = os.path.join(root, f)
            size = os.path.getsize(fp)
            print(f"{indent}  {f} ({size} bytes)")

    print()
    print("✅ E2E 测试完成")

except Exception as e:
    elapsed = time.time() - start
    print(f"\n❌ 测试失败 (耗时 {elapsed:.1f}s): {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
