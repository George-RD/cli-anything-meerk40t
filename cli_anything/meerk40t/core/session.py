import json
import os
import time


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
                self.name = data.get("name", "Untitled")
                self.svg_path = data.get("svg_path")
                self.modified = data.get("modified", False)
                self.history = data.get("history", [])
                self.undo_stack = data.get("undo_stack", [])
                self.redo_stack = data.get("redo_stack", [])
            except Exception:
                pass

    def save(self, backend=None):
        data = {
            "name": self.name,
            "svg_path": self.svg_path,
            "modified": self.modified,
            "history": self.history,
            "undo_stack": self.undo_stack,
            "redo_stack": self.redo_stack,
        }
        self._locked_save_json(self.session_path, data)
        if backend is not None and self.svg_path:
            from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend
            backend.save_svg(self.svg_path, "default")
        return {"saved": True, "path": self.session_path}

    @staticmethod
    def _locked_save_json(path, data):
        try:
            import fcntl

            with open(path, "w", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.truncate()
                    f.seek(0)
                    json.dump(data, f, indent=2)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

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
