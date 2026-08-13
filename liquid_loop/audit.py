from __future__ import annotations

import os
import hashlib

from .textutil import (
    now, uid, _derive_lifecycle_thresholds, _get_version,
    _tokenize, _keyword_overlap, _judge_answer,
    _dissolve_votes_path, _load_dissolve_votes, _save_dissolve_votes,
)

class AuditChain:
    """轻量级审计链：每次变更追加 SHA256 链式哈希"""

    def __init__(self, audit_path: str):
        self._path = audit_path
        self._chain: list[str] = []
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            with open(self._path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        parts = line.split("|")
                        if len(parts) >= 2:
                            self._chain.append(parts[1])

    def append(self, event_type: str, data: str) -> str:
        prev = self._chain[-1] if self._chain else "genesis"
        chain_hash = hashlib.sha256(f"{event_type}:{data}:{prev}".encode()).hexdigest()[:16]
        self._chain.append(chain_hash)
        ts = now()
        with open(self._path, "a") as f:
            f.write(f"{ts}|{chain_hash}|{event_type}|{data}|{prev}\n")
        return chain_hash

    @property
    def root(self) -> str:
        return self._chain[-1] if self._chain else "genesis"

    def verify(self, entry: str) -> bool:
        """验证某条完整日志行是否匹配链中记录"""
        if "|" not in entry:
            return False
        parts = entry.strip().split("|")
        if len(parts) < 5:
            return False
        _, stored_hash, event_type, data, prev = parts[:5]
        expected = hashlib.sha256(f"{event_type}:{data}:{prev}".encode()).hexdigest()[:16]
        return expected == stored_hash
