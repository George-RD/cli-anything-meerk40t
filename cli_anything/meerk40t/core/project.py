from __future__ import annotations

import os
import tempfile

from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend


def create_project(backend, name="Untitled"):
    _clear_elements_tree(backend)
    return {"name": name, "elements": 0, "operations": 0}


def _remove_node(node):
    try:
        node.remove_node()
    except Exception:
        try:
            node.remove()
        except Exception:
            pass


def _clear_elements_tree(backend):
    """Clear all elements AND operations from the tree.

    The kernel auto-creates a default operation set at boot. If we don't
    clear ops before loading a saved SVG, the loaded ops pile on top of the
    defaults and accumulate across open/save cycles.
    """
    backend.run("elements clear all")
    try:
        backend.elements.op_branch.remove_all_children()
    except Exception:
        pass
    if backend.elem_count() > 0:
        try:
            backend.elements.clear_elements()
        except Exception:
            pass
    if backend.elem_count() > 0:
        for node in list(backend.elems()):
            _remove_node(node)


def _scene_nodes(backend):
    """Live references to every element/op node currently in the tree."""
    nodes = []
    try:
        nodes.extend(backend.elements.ops())
    except Exception:
        pass
    try:
        nodes.extend(backend.elems())
    except Exception:
        try:
            nodes.extend(backend.elements.elems())
        except Exception:
            pass
    return nodes


def open_project(backend, path):
    """Open an SVG project transactionally.

    Failure (missing scene, loader error, empty/invalid inventory) leaves the
    prior scene and prior files byte-identical. The candidate is loaded by
    appending, then only the pre-existing nodes are stripped on rollback, or
    only the candidate's nodes remain on commit. The target file is never
    written here.
    """
    if not os.path.exists(path):
        # Fresh project: cleared tree, bound to the (not-yet-existing) path.
        _clear_elements_tree(backend)
        return {
            "path": path,
            "elements": backend.elem_count(),
            "operations": backend.op_count(),
        }
    pre_existing = _scene_nodes(backend)
    pre_ids = {id(n) for n in pre_existing}
    try:
        backend.load_file(path)
    except Exception as exc:
        # Rollback: drop anything the failed load may have appended.
        for n in _scene_nodes(backend):
            if id(n) not in pre_ids:
                _remove_node(n)
        return {"ok": False, "error": f"open failed: {exc}", "path": path}
    loaded = [n for n in _scene_nodes(backend) if id(n) not in pre_ids]
    if not loaded:
        # Empty/invalid inventory: nothing was staged; keep the prior scene.
        for n in _scene_nodes(backend):
            if id(n) not in pre_ids:
                _remove_node(n)
        return {
            "ok": False,
            "error": f"open loaded no scene from {path}",
            "path": path,
        }
    # Commit: remove the prior scene, leaving only the candidate.
    for n in pre_existing:
        _remove_node(n)
    return {
        "path": path,
        "elements": backend.elem_count(),
        "operations": backend.op_count(),
    }


def save_project(backend, path, version="default"):
    """Save the current project atomically.

    Render to a same-directory temp file, verify non-empty, then
    ``os.replace`` onto the target so a prior target is byte-identical on any
    earlier failure.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(suffix=".svg", prefix=".mkproj-", dir=directory)
    os.close(fd)
    replaced = False
    try:
        ok = backend.save_svg(tmp, version)
        if not ok:
            return {"ok": False, "error": f"save verification failed for {path}"}
        if os.path.getsize(tmp) == 0:
            return {"ok": False, "error": f"save produced empty file for {path}"}
        os.replace(tmp, path)
        replaced = True
    except Exception as exc:
        return {"ok": False, "error": f"save failed: {exc}", "path": path}
    finally:
        # The temp is only safe to drop once it has been atomically replaced;
        # on any early return or exception it must be removed so a stale
        # .mkproj-* file never lingers in the target directory.
        if not replaced and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0
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
    """Close the project, failing closed if the tree will not clear."""
    _clear_elements_tree(backend)
    if backend.elem_count() > 0:
        return {
            "ok": False,
            "error": "failed to clear project tree on close",
            "closed": False,
        }
    return {"closed": True, "autosave_path": None}
