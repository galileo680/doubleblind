import copy
import difflib
import io
from typing import Any

import jsonpatch
from ruamel.yaml import YAML

from doubleblind_broker.presets import Preset

PROTECTED_PATHS = ("/doubleblind", "/id", "/version", "/status", "/parties")


class PatchError(Exception):
    pass


def skeleton(preset: Preset) -> dict[str, Any]:
    return {
        "doubleblind": "1.0",
        "id": preset.contract_id,
        "version": 1,
        "status": "draft",
        "parties": {
            "consumer": {"id": preset.consumer.party, "name": preset.consumer.display_name},
            "provider": {"id": preset.provider.party, "name": preset.provider.display_name},
        },
        "environments": {},
        "operations": {},
        "scenarios": [],
    }


def dump_yaml(data: dict[str, Any]) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.width = 4096
    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def touches_protected_path(path: str) -> bool:
    return any(
        path == protected or path.startswith(f"{protected}/") for protected in PROTECTED_PATHS
    )


def apply_patches(contract: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    for patch in patches:
        for key in ("path", "from"):
            target = patch.get(key)
            if isinstance(target, str) and touches_protected_path(target):
                raise PatchError(
                    f"patch {patch.get('op')} {target}: this field is managed by the broker"
                )
    try:
        result = jsonpatch.apply_patch(copy.deepcopy(contract), patches)
    except (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException, TypeError) as error:
        raise PatchError(f"patch cannot be applied: {error}") from error
    if not isinstance(result, dict):
        raise PatchError("patches must keep the contract a mapping")
    return result


def with_version(contract: dict[str, Any], version: int, status: str) -> dict[str, Any]:
    updated = copy.deepcopy(contract)
    updated["version"] = version
    updated["status"] = status
    return updated


def human_decision_id(contract: dict[str, Any], question_id: str) -> str:
    used = {str(item.get("id")) for item in contract.get("decisions", []) if isinstance(item, dict)}
    base = f"H{question_id.removeprefix('Q')}"
    candidate, suffix = base, 1
    while candidate in used:
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def add_decision(contract: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(contract)
    updated.setdefault("decisions", []).append(decision)
    return updated


def mark_signed(contract: dict[str, Any], party_keys: list[str]) -> dict[str, Any]:
    updated = copy.deepcopy(contract)
    updated["status"] = "signed"
    for decision in updated.get("decisions", []):
        decision["approved_by"] = list(party_keys)
    return updated


def unified_diff(old: str, new: str, old_label: str, new_label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=old_label,
            tofile=new_label,
        )
    )


def diff_stats(old: str, new: str) -> dict[str, int]:
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"lines_added": added, "lines_removed": removed}


def patch_summary(patches: list[dict[str, Any]]) -> list[str]:
    return [f"{patch.get('op')} {patch.get('path')}" for patch in patches]
