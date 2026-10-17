"""Import only the selected DeepSeek entry. Never print or commit API credentials."""

import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from self_agent.auth import password_hash  # noqa: E402


def main():
    config_file = ROOT / ".env"
    if config_file.exists():
        print("已有 .env，保持现有配置。")
        return
    database = Path.home() / ".cc-switch/cc-switch.db"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT settings_config FROM providers WHERE name='DeepSeek' AND app_type='codex'"
        ).fetchone()
        if not row:
            raise SystemExit("CC Switch 中未找到 Codex DeepSeek 配置")
        config = json.loads(row[0])
    key = config.get("auth", {}).get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("DeepSeek 配置缺少 API key")
    import re

    raw = config["config"]
    model = re.search(r'^model\s*=\s*"([^"]+)"', raw, re.M)
    url = re.search(r'^base_url\s*=\s*"([^"]+)"', raw, re.M)
    password = secrets.token_urlsafe(20)
    values = {
        "DEEPSEEK_API_KEY": key,
        "DEEPSEEK_BASE_URL": url.group(1) if url else "https://api.deepseek.com",
        "CHAT_MODEL": model.group(1) if model else "deepseek-v4-flash",
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "LOGIN_PASSWORD_HASH": password_hash(password),
        "SECURE_COOKIE": "false",
        "APP_BASE_PATH": "/",
        "JEV_ENV_FILE": str(Path.home() / ".config/jev/.env"),
    }
    fd = os.open(config_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        for name, value in values.items():
            f.write(name + "=" + str(value) + "\n")
    credentials = ROOT / ".credentials-login.txt"
    fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(
            "随心记\n访问地址：https://example.com/self-agent/\n登录密码："
            + password
            + "\n"
        )
    print("已导入 DeepSeek 配置；登录密码保存在权限为 600 的 .credentials-login.txt。")


if __name__ == "__main__":
    main()
