from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend


def create_project(backend, name="Untitled"):
    _clear_elements_tree(backend)
    return {"name": name, "elements": 0, "operations": 0}


def _clear_elements_tree(backend):
    """Clear all elements AND operations from the tree.

    The kernel auto-creates a default operation set at boot. If we don't
    clear ops before loading a saved SVG, the loaded ops pile on top of the
    defaults and accumulate across open/save cycles.
    """
    # Clear elements via console command.
    backend.run("elements clear all")
    # Clear operations by emptying the op branch directly.
    try:
        backend.elements.op_branch.remove_all_children()
    except Exception:
        pass
    # Fallback: remove any remaining elements node-by-node.
    if backend.elem_count() > 0:
        try:
            backend.elements.clear_elements()
        except Exception:
            pass
    if backend.elem_count() > 0:
        for node in list(backend.elems()):
            try:
                node.remove_node()
            except Exception:
                try:
                    node.remove()
                except Exception:
                    pass


def open_project(backend, path):
    """Open an SVG project file, or start a fresh project bound to the path
    if the file does not yet exist.

    The tree is cleared BEFORE loading so that auto-created default ops do
    not accumulate on top of the ops already stored in the SVG.
    """
    import os
    if os.path.exists(path):
        _clear_elements_tree(backend)
        backend.load_file(path)
    else:
        _clear_elements_tree(backend)
    return {"path": path, "elements": backend.elem_count(), "operations": backend.op_count()}


def save_project(backend, path, version="default"):
    ok = backend.save_svg(path, version)
    size = 0
    try:
        import os
        size = os.path.getsize(path)
    except Exception:
        pass
    return {
        "path": path,
        "size_bytes": size,
        "elements": backend.elem_count(),
        "version": version,
    }


def project_info(backend):
    return {
        "elements": backend.elem_count(),
        "operations": backend.op_count(),
        "device": str(backend.device()),
    }


def close_project(backend):
    _clear_elements_tree(backend)
    return {"closed": True}
