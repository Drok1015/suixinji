"""Export a reviewed source snapshot without local credentials or deployment identities."""

import io
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_NAME = re.compile(r"key|secret|password|token|credential", re.I)
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
]


def private_values():
    """Read known local secrets without printing any values."""
    values = set()
    env_files = [ROOT / ".env", Path.home() / ".config/jev/.env"]
    primary = ROOT / ".env"
    if primary.exists():
        match = re.search(r"^JEV_ENV_FILE=(.+)$", primary.read_text(), re.M)
        if match:
            env_files.append(Path(match[1].strip().strip("\"'")).expanduser())
    for path in env_files:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            name, sep, value = line.partition("=")
            value = value.strip().strip("\"'")
            if sep and SENSITIVE_NAME.search(name) and len(value) >= 12:
                values.add(value)

    def collect(obj):
        if isinstance(obj, dict):
            for name, value in obj.items():
                if SENSITIVE_NAME.search(name) and isinstance(value, str) and len(value) >= 12:
                    values.add(value)
                collect(value)
        elif isinstance(obj, list):
            for value in obj:
                collect(value)

    for path in ROOT.glob(".credentials*.json"):
        collect(json.loads(path.read_text()))
    return values


def source_files():
    # An isolated Git directory applies repository ignore rules without modifying this project.
    with tempfile.TemporaryDirectory(prefix="suixinji-export-") as directory:
        subprocess.run(["git", "init", "--quiet", directory], check=True)
        result = subprocess.run(
            ["git", f"--git-dir={directory}/.git", f"--work-tree={ROOT}",
             "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=ROOT, check=True, capture_output=True,
        )
    return sorted(Path(name) for name in result.stdout.decode().split("\0") if name)


def public_replacements():
    """Discover local deployment identities, then scrub only the export copy."""
    replacements = {}
    for relative in ("mobile/common/api.uts", "mobile/vite.config.js"):
        for url in re.findall(r"https?://[^\s'\"<>]+", (ROOT / relative).read_text()):
            host = urlsplit(url).hostname
            if host and host not in {"localhost", "127.0.0.1"}:
                replacements[host] = "example.com"
    manifest = json.loads((ROOT / "mobile/manifest.json").read_text())
    replacements[manifest["appid"]] = "__UNI__YOUR_APP_ID"
    replacements[manifest["app-harmony"]["distribute"]["bundleName"]] = "com.example.suixinji"
    return replacements


def main():
    secrets = private_values()
    replacements = public_replacements()
    files = source_files()
    output = io.BytesIO()
    failures = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            path = ROOT / relative
            if path.is_symlink():
                failures.append(f"{relative}: symbolic link requires manual review")
                continue
            content = path.read_bytes()
            text = content.decode("utf-8", errors="ignore")
            if any(value in text for value in secrets) or any(p.search(text) for p in SECRET_PATTERNS):
                failures.append(f"{relative}: possible credential detected")
                continue
            # Source text only; existing binary art is kept byte-for-byte.
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                for old, new in sorted(replacements.items(), key=lambda item: -len(item[0])):
                    text = text.replace(old, new)
                text = re.sub(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])",
                              lambda m: m[0] if m[0].startswith(("127.", "0.")) else "192.0.2.1", text)
                text = re.sub(r"https://www\.pgyer\.com/(?!apiv2/|doc/)[\w-]+",
                              "https://www.pgyer.com/your-app", text)
                content = text.encode()
            archive.writestr("suixinji/" + relative.as_posix(), content)
    if failures:
        raise SystemExit("Export blocked; inspect these files (values hidden):\n" + "\n".join(failures))
    target = ROOT / "dist/suixinji-source.zip"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(output.getvalue())
    print(f"Exported {len(files)} files: {target}")
    print("Excluded credentials, local data, signing material, screenshots and build outputs via .gitignore.")
    print("Replaced private deployment addresses and app identifiers in the export copy only.")
    print("No known local secret values or common credential patterns detected in included files.")


if __name__ == "__main__":
    main()
