from functools import lru_cache
import hashlib
import json
import time

# Small process-local TTL cache; no listing content is persisted to disk.
_CACHE: dict[str, tuple[float, object]] = {}

def cache_key(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def get_cached(key: str, ttl: int = 300):
    item = _CACHE.get(key)
    if not item:
        return None
    timestamp, value = item
    if time.time() - timestamp > ttl:
        _CACHE.pop(key, None)
        return None
    return value

def set_cached(key: str, value: object):
    if len(_CACHE) > 256:
        now = time.time()
        for old_key, (stamp, _) in list(_CACHE.items()):
            if now - stamp > 300:
                _CACHE.pop(old_key, None)
        while len(_CACHE) > 256:
            _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (time.time(), value)
