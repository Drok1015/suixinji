import json
from pathlib import Path
from urllib.parse import urlparse


def harmony_release(path: str, current: int) -> dict:
    """Only expose verified publication metadata; credentials never reach clients."""
    empty = {"available": False, "versionCode": 0, "versionName": "", "notes": "", "url": ""}
    try:
        data = json.loads(Path(path).read_text())
        code = int(data["versionCode"])
        url = urlparse(data["url"])
        if (data["bundleName"] != "com.example.suixinji" or code < 1
                or url.scheme != "https" or url.netloc != "www.pgyer.com"
                or url.username or url.password):
            return empty
        return {"available": code > current, "versionCode": code,
                "versionName": str(data["versionName"]), "notes": str(data.get("notes", "")),
                "url": data["url"]}
    except (OSError, ValueError, KeyError, TypeError):
        return empty
