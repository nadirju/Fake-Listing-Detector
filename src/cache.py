"""Thread-safe in-memory TTL cache (keyed by normalized URL / evidence fingerprint)."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple


class TTLCache:
    def __init__(self, ttl_seconds: float, maxsize: int = 256, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl_seconds
        self.maxsize = maxsize
        self._clock = clock
        self._data: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < self._clock():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (self._clock() + self.ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


def fingerprint(obj: Any) -> str:
    """Stable short hash of any JSON-serialisable evidence."""
    blob = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


_CACHES: Dict[str, TTLCache] = {}
_CACHES_LOCK = threading.Lock()


def get_cache(name: str, ttl_seconds: float, maxsize: int = 256) -> TTLCache:
    """Process-wide named cache (survives Streamlit reruns)."""
    with _CACHES_LOCK:
        cache = _CACHES.get(name)
        if cache is None or cache.ttl != ttl_seconds:
            cache = TTLCache(ttl_seconds, maxsize)
            _CACHES[name] = cache
        return cache


def clear_all_caches() -> None:
    with _CACHES_LOCK:
        for cache in _CACHES.values():
            cache.clear()
