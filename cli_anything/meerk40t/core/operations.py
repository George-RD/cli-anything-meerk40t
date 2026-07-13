from __future__ import annotations

import math
from typing import Any

from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend

# Operation types that provably create exactly one operation node via the
# kernel console. ``image`` is intentionally excluded: ``backend.run("image")``
# is a silent no-op, so shipping it would report success without mutating.
SUPPORTED_ADD_TYPES = frozenset({"cut", "engrave", "raster", "dots"})

# Per-property schema for ``operations set``. Only these keys are settable;
# unknown keys are rejected.
_PROPERTY_SCHEMA: dict[str, dict[str, Any]] = {
    "power": {"kind": "number", "min": 1.0, "max": 1000.0},
    "speed": {"kind": "number", "min": 0.0, "exclusive_min": True},
    "passes": {"kind": "int", "min": 1},
    "output": {"kind": "bool"},
    "label": {"kind": "str"},
}
# Numeric bounds mirror the material-record invariants (power 1..1000,
# speed > 0, passes >= 1). Fractional passes (e.g. "2.5") are rejected, not
# truncated, because the field is an integer count of physical burns.


def _coerce_number(raw: Any, *, exclusive_min: bool, minimum: float | None, maximum: float | None):
    if isinstance(raw, bool):
        raise ValueError("boolean is not a number")
    if isinstance(raw, str):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"expected a number, got {raw!r}")
    elif isinstance(raw, (int, float)):
        value = float(raw)
    else:
        raise ValueError(f"expected a number, got {raw!r}")
    if not math.isfinite(value):
        raise ValueError(f"value must be finite, got {raw!r}")
    if maximum is not None and value > maximum:
        raise ValueError(f"value {value} exceeds maximum {maximum}")
    if minimum is not None:
        if exclusive_min and value <= minimum:
            raise ValueError(f"value {value} must be greater than {minimum}")
        if not exclusive_min and value < minimum:
            raise ValueError(f"value {value} is below minimum {minimum}")
    return value


def _coerce_int(raw: Any, minimum: int):
    if isinstance(raw, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(raw, str):
        s = raw.strip()
        try:
            value = int(s)
        except (TypeError, ValueError):
            try:
                f = float(s)
            except (TypeError, ValueError):
                raise ValueError(f"expected an integer, got {raw!r}")
            if not math.isfinite(f) or math.floor(f) != f:
                raise ValueError(f"expected an integer, got {raw!r}")
            value = int(f)
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not math.isfinite(raw):
            raise ValueError(f"value must be finite, got {raw!r}")
        if math.floor(raw) != raw:
            raise ValueError(f"expected an integer, got {raw!r}")
        value = int(raw)
    else:
        raise ValueError(f"expected an integer, got {raw!r}")
    if value < minimum:
        raise ValueError(f"value {value} is below minimum {minimum}")
    return value


def _coerce_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    raise ValueError(f"expected a boolean, got {raw!r}")


def _coerce_str(raw: Any) -> str:
    value = raw if isinstance(raw, str) else str(raw)
    value = value.strip()
    if not value:
        raise ValueError("label must be a non-empty string")
    return value


def _validate_property(key: str, raw: Any):
    """Return (coerced_value, None) or (None, error_message)."""
    spec = _PROPERTY_SCHEMA.get(key)
    if spec is None:
        return None, f"unsupported property {key!r}; supported: {sorted(_PROPERTY_SCHEMA)}"
    kind = spec["kind"]
    try:
        if kind == "number":
            return (
                _coerce_number(
                    raw,
                    exclusive_min=spec.get("exclusive_min", False),
                    minimum=spec.get("min"),
                    maximum=spec.get("max"),
                ),
                None,
            )
        if kind == "int":
            return _coerce_int(raw, minimum=spec["min"]), None
        if kind == "bool":
            return _coerce_bool(raw), None
        if kind == "str":
            return _coerce_str(raw), None
    except ValueError as exc:
        return None, str(exc)
    return None, f"unsupported property kind {kind!r}"


def list_operations(backend):
    result = []
    for node in backend.ops():
        info = {
            "id": getattr(node, "id", None),
            "type": getattr(node, "type", None),
            "label": getattr(node, "label", None),
            "output": getattr(node, "output", None),
            "speed": getattr(node, "speed", None),
            "power": getattr(node, "power", None),
        }
        result.append(info)
    return result


def add_operation(backend, op_type):
    if op_type not in SUPPORTED_ADD_TYPES:
        return {
            "error": f"unknown operation type {op_type!r}; supported: {sorted(SUPPORTED_ADD_TYPES)}",
            "category": "user",
        }
    before = backend.op_count()
    backend.run(op_type)
    after = backend.op_count()
    if after != before + 1:
        return {
            "error": f"backend did not create an operation for {op_type!r} "
            f"(count {before}->{after}); the operation is not applied",
            "category": "backend",
        }
    return {"added": True, "type": op_type, "total_ops": after}


def classify_elements(backend):
    before = backend.op_count()
    backend.run("element* classify")
    return {"classified": True, "total_ops": backend.op_count(), "ops_before": before}


def declassify_elements(backend):
    before = backend.op_count()
    backend.run("element* declassify")
    return {"declassified": True, "total_ops": backend.op_count(), "ops_before": before}


def set_operation(backend, index, key, value):
    ops = backend.ops()
    if not (0 <= index < len(ops)):
        return {
            "error": f"operation index {index} out of range (have {len(ops)} operations)",
            "category": "user",
        }
    coerced, err = _validate_property(key, value)
    if err is not None:
        return {"error": err, "category": "user"}
    node = ops[index]
    setattr(node, key, coerced)
    read_back = getattr(node, key, None)
    if read_back != coerced and not (
        isinstance(coerced, float) and isinstance(read_back, (int, float)) and math.isclose(read_back, coerced)
    ):
        return {
            "error": f"backend rejected set {key!r}={value!r} (read back {read_back!r})",
            "category": "backend",
        }
    return {
        "set": True,
        "index": index,
        "key": key,
        "value": value,
        "validated": coerced,
        "read_back": read_back,
    }


def delete_operation(backend, index):
    ops = backend.ops()
    if not (0 <= index < len(ops)):
        return {
            "error": f"operation index {index} out of range (have {len(ops)} operations)",
            "category": "user",
        }
    before = backend.op_count()
    ops[index].remove_node()
    after = backend.op_count()
    if after != before - 1:
        return {
            "error": f"backend did not remove operation {index} (count {before}->{after})",
            "category": "backend",
        }
    return {"deleted": True, "index": index, "total_ops": after}


def clear_operations(backend):
    before = backend.op_count()
    backend.elements.op_branch.remove_all_children()
    after = backend.op_count()
    if after != 0:
        return {
            "error": f"backend did not clear all operations (count {before}->{after})",
            "category": "backend",
        }
    return {"cleared": True, "total_ops": 0}
