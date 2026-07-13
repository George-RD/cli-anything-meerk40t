import json
import os
import tempfile
import time

from cli_anything.meerk40t.utils.meerk40t_backend import BackendError
from .project import open_project
from cli_anything.meerk40t.utils.atomic_io import atomic_write_json


class Session:
    def __init__(self, session_path):
        self.session_path = session_path
        self.name = "Untitled"
        self.svg_path = None
        self.modified = False
        self.history = []
        self.undo_stack = []
        self.redo_stack = []
        if os.path.exists(session_path):
            try:
                with open(session_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                raise BackendError(
                    f"corrupt session file {session_path}: {exc}",
                    path=session_path,
                )
            self.name = data.get("name", "Untitled")
            self.svg_path = data.get("svg_path")
            self.modified = data.get("modified", False)
            self.history = data.get("history", [])
            self.undo_stack = data.get("undo_stack", [])
            self.redo_stack = data.get("redo_stack", [])

    def save(self, backend=None):
        data = {
            "name": self.name,
            "svg_path": self.svg_path,
            "modified": self.modified,
            "history": self.history,
            "undo_stack": self.undo_stack,
            "redo_stack": self.redo_stack,
        }
        # Coordinated: surface a session-SVG save failure BEFORE writing the
        # JSON so a half-persisted session is never reported as saved.
        if backend is not None and self.svg_path:
            try:
                ok = backend.save_svg(self.svg_path, "default")
            except Exception as exc:
                raise BackendError(
                    f"failed to save session SVG: {exc}", path=self.svg_path
                ) from exc
            if not ok:
                raise BackendError(
                    "failed to save session SVG: verification returned false",
                    path=self.svg_path,
                )
        self._locked_save_json(self.session_path, data)
        self.modified = False
        return {"saved": True, "path": self.session_path}

    def _locked_save_json(self, path, data):
        # Shared atomic+durable persistence primitive (also used for material
        # profiles), so session state and material writes keep one convention.
        atomic_write_json(path, data)

    def record_command(self, cmd):
        self.history.append({"cmd": cmd, "ts": time.time()})
        self.undo_stack.append(cmd)
        self.redo_stack = []
        self.modified = True

    def undo(self):
        if not self.undo_stack:
            return None
        cmd = self.undo_stack.pop()
        self.redo_stack.append(cmd)
        return cmd

    def redo(self):
        if not self.redo_stack:
            return None
        cmd = self.redo_stack.pop()
        self.undo_stack.append(cmd)
        return cmd

    def status(self):
        return {
            "name": self.name,
            "svg_path": self.svg_path,
            "modified": self.modified,
            "history_count": len(self.history),
            "undo_count": len(self.undo_stack),
            "redo_count": len(self.redo_stack),
        }

    def restore(self, backend):
        """Reload the recorded SVG via the transactional open path."""
        if not self.svg_path:
            return {"ok": False, "error": "no svg_path recorded in session"}
        return open_project(backend, self.svg_path)
