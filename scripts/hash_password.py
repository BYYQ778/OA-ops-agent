"""生成 PBKDF2 密码哈希（用于 config.yaml auth.users[].password_hash）。

用法:
    python scripts/hash_password.py "你的密码"
    python scripts/hash_password.py            # 交互式输入（不回显）

输出示例:
    pbkdf2_sha256$200000$<salt>$<digest>
把输出整行填到 config.yaml 的 auth.users[].password_hash 即可（比明文更安全）。
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:  # 脚本直跑时保证可导入 utils
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.security import hash_password  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    password = args[0] if args else getpass.getpass("请输入密码（不回显）: ")
    if not password:
        print("错误：密码为空", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
