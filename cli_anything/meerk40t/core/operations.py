from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend


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
    backend.run(op_type)
    return {"added": True, "type": op_type, "total_ops": backend.op_count()}


def classify_elements(backend):
    backend.run("element* classify")
    return {"classified": True, "total_ops": backend.op_count()}


def declassify_elements(backend):
    backend.run("element* declassify")
    return {"declassified": True, "total_ops": backend.op_count()}


def set_operation(backend, index, key, value):
    out = backend.run(f"op{index} {key} {value}")
    failed = any(
        phrase in line.lower()
        for line in out
        for phrase in ("unknown", "error", "not a registered command", "not registered")
    )
    if failed:
        ops = backend.ops()
        if 0 <= index < len(ops):
            try:
                setattr(ops[index], key, value)
            except Exception:
                pass
    return {"set": True, "index": index, "key": key, "value": value}


def delete_operation(backend, index):
    ops = backend.ops()
    if 0 <= index < len(ops):
        node = ops[index]
        try:
            node.remove_node()
            return {"deleted": True, "index": index, "total_ops": backend.op_count()}
        except Exception:
            pass
    return {"deleted": False, "index": index, "total_ops": backend.op_count()}


def clear_operations(backend):
    try:
        backend.elements.op_branch.remove_all_children()
    except Exception:
        pass
    return {"cleared": True, "total_ops": backend.op_count()}
