#!/usr/bin/env python3
"""Prompt on the server's terminal; never accept or print a secret argument."""
import getpass
import os
from pathlib import Path
import sys
import tempfile


def main():
    if len(sys.argv) != 2 or not sys.stdin.isatty():
        raise SystemExit("请在交互式终端运行：python3 scripts/set_outlook_secret.py /path/to/.env")
    path = Path(sys.argv[1]).resolve(strict=True)
    secret = getpass.getpass("粘贴 Microsoft 客户端密码的值（输入不显示）：").strip()
    if not secret or any(char in secret for char in "\r\n\0"):
        raise SystemExit("客户端密码不能为空，也不能包含换行。")
    original = path.read_text()
    updates = {"EMAIL_ENABLED": "1", "EMAIL_BACKEND": "box.graph_backend.EmailBackend", "MS_GRAPH_CLIENT_SECRET": secret}
    lines = []
    for line in original.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key not in updates:
            lines.append(line)
    lines.extend(f"{key}={value}" for key, value in updates.items())
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".env-before-outlook-", delete=False) as backup:
        backup.write(original.encode())
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".env-outlook-", delete=False) as out:
            temp_path = out.name
            out.write(("\n".join(lines) + "\n").encode())
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    print("客户端密码已保存。请重建 web / worker 后到主人后台连接邮箱。")


if __name__ == "__main__":
    main()
