"""Cross-flow arbitration added solely for Banco de Chile transitions.

Legacy VPN operations may retain their original concurrency semantics with one
another.  This arbiter only prevents a Banco transition and a legacy
transition from being launched at the same time.
"""
from __future__ import annotations

import threading


class BancoChileActionArbiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bancochile_active = False
        self._legacy_active = 0

    def begin_bancochile(self) -> bool:
        with self._lock:
            if self._bancochile_active or self._legacy_active:
                return False
            self._bancochile_active = True
            return True

    def finish_bancochile(self) -> None:
        with self._lock:
            self._bancochile_active = False

    def begin_legacy(self) -> bool:
        with self._lock:
            if self._bancochile_active:
                return False
            self._legacy_active += 1
            return True

    def finish_legacy(self) -> None:
        with self._lock:
            if self._legacy_active:
                self._legacy_active -= 1

    @property
    def bancochile_active(self) -> bool:
        with self._lock:
            return self._bancochile_active

    @property
    def legacy_active(self) -> int:
        with self._lock:
            return self._legacy_active
