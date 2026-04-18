import json
import hashlib
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _key(name: str, payload) -> Path:
    h = hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:10]
    return CACHE_DIR / f"{name}_{h}.json"


def get(name: str, payload):
    path = _key(name, payload)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def put(name: str, payload, value):
    path = _key(name, payload)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    return value
