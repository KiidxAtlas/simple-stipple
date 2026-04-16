"""UX consistency audit utilities for constraint-based interaction model."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Violation:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionAuditInput:
    action_name: str
    user_action: str
    touched_primitives: set[str]
    dependency_edges: set[tuple[str, str]]
    before_state: dict[str, Any]
    after_state: dict[str, Any]
    ui_element_mappings: dict[str, str]
    direct_manipulation: bool = True


@dataclass
class ActionAuditResult:
    action_name: str
    changed_keys: set[str]
    reachable_keys: set[str]
    violations: list[Violation]

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0


def _flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            flat.update(_flatten(v, key))
        return flat
    if isinstance(data, list):
        for i, v in enumerate(data):
            key = f"{prefix}[{i}]"
            flat.update(_flatten(v, key))
        if not data:
            flat[prefix] = []
        return flat
    flat[prefix] = data
    return flat


def _reachable(start: set[str], edges: set[tuple[str, str]]) -> set[str]:
    adj: dict[str, set[str]] = defaultdict(set)
    for src, dst in edges:
        adj[src].add(dst)

    seen: set[str] = set(start)
    queue: deque[str] = deque(start)
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, set()):
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return seen


def audit_action_consistency(inp: ActionAuditInput) -> ActionAuditResult:
    before = _flatten(inp.before_state)
    after = _flatten(inp.after_state)

    keys = set(before.keys()) | set(after.keys())
    changed = {k for k in keys if before.get(k) != after.get(k)}
    reachable = _reachable(inp.touched_primitives, inp.dependency_edges)

    violations: list[Violation] = []

    # 1) Hidden state changes
    hidden = sorted(changed - reachable)
    if hidden:
        violations.append(
            Violation(
                code="HID-001",
                message="Hidden state changes detected outside explicit propagation graph.",
                details={"keys": hidden},
            )
        )

    # 2) Non-local effects
    non_local = sorted(changed - inp.touched_primitives - reachable)
    if non_local:
        violations.append(
            Violation(
                code="NLC-001",
                message="Non-local effects without explicit dependency relationship.",
                details={"keys": non_local},
            )
        )

    # 3) UI orphan controls
    missing_mappings = [
        ui_name
        for ui_name, state_key in inp.ui_element_mappings.items()
        if state_key not in before and state_key not in after
    ]
    if missing_mappings:
        violations.append(
            Violation(
                code="MAP-001",
                message="UI elements without valid state/dependency mapping.",
                details={"elements": missing_mappings},
            )
        )

    # 4) Indirect manipulation warning
    if not inp.direct_manipulation and inp.touched_primitives:
        violations.append(
            Violation(
                code="DIR-001",
                message="Action uses indirect manipulation where direct manipulation is available.",
                details={"action": inp.action_name},
            )
        )

    return ActionAuditResult(
        action_name=inp.action_name,
        changed_keys=changed,
        reachable_keys=reachable,
        violations=violations,
    )


def audit_feature_set(inputs: list[ActionAuditInput]) -> dict[str, ActionAuditResult]:
    return {inp.action_name: audit_action_consistency(inp) for inp in inputs}
