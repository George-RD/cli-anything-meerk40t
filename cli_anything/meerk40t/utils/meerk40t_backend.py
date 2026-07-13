"""MeerK40t headless backend wrapper.

Boots the real MeerK40t kernel in headless mode (same code path as `meerk40t -z`)
and exposes a stable interface for the CLI harness. All commands are executed via
`kernel.console()` against the actual kernel — this is a wrapper, not a
reimplementation.
"""

from __future__ import annotations

import os
import re
import threading
import secrets
import xml.etree.ElementTree as ET


class BackendError(Exception):
    """Base class for backend persistence failures.

    ``category`` is "infrastructure" for failures outside the user's control
    (disk, permissions) and "user" for failures caused by bad input/state.
    """

    category = "infrastructure"

    def __init__(self, message=None, *, path=None, reason=None):
        self.path = path
        self.reason = reason
        super().__init__(message or reason or "backend error")


class SaveVerificationError(BackendError):
    """The saved artifact failed post-write verification."""

    category = "user"


class LoadError(BackendError):
    """Loading a project file failed."""

    category = "user"


def _looks_like_error(line: str) -> bool:
    """True only for lines that begin with a MeerK40t error diagnostic.

    Matches on the leading token so an echoed ``load /path/exception.svg`` (the
    command echo captured by ``run``) or a path containing ``error`` is never
    misclassified as a load failure.
    """
    s = line.strip().lower()
    if not s:
        return False
    return s.startswith(
        ("error", "traceback", "exception", "cannot ", "unable to", "failed to")
    )

from typing import Any, Optional


_ANSI_RE = re.compile(r"\033\[[^m]*m")
_TS_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s?")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _strip_ts(text: str) -> str:
    """Strip the leading timestamp prefix the console channel adds."""
    return _TS_RE.sub("", text)
# Map the user-facing CLI device choice to the provider name MeerK40t
# actually registers for `service device start -i <provider>`. Verified against
# the installed meerk40t package (meerk40t/<driver>/plugin.py):
#   lihuiyu  -> lhystudios   (provider/device/lhystudios -> LihuiyuDevice)
#   moshi    -> moshi        (provider/device/moshi -> MoshiDevice)
#   ruida    -> ruida         (provider/device/ruida -> RuidaDevice)
#   newly    -> newly         (provider/device/newly -> NewlyDevice)
#   balor    -> balor         (provider/device/balor -> BalorDevice)
#   grbl     -> grbl          (provider/device/grbl -> GRBLDevice)
#   dummy    -> dummy        (handled separately via `service device start dummy 0`)
# Only `lihuiyu` differs from its registered provider name.
_DEVICE_PROVIDER_ALIASES = {
    "lihuiyu": "lhystudios",
}


def _device_provider_name(device_type: str) -> str:
    """Resolve a user-facing CLI device choice to its MeerK40t provider name.

    `service device start -i <provider>` needs the provider name MeerK40t
    registered, not the friendly CLI choice. Unknown values pass through
    unchanged so future providers work without code changes here.
    """
    return _DEVICE_PROVIDER_ALIASES.get(device_type, device_type)


class Meerk40tBackend:
    """Headless MeerK40t kernel wrapper.

    Boots a real Kernel instance with the core, device, svg, dxf, image, fill and
    driver plugins (the same set as ``test/bootstrap.py``), captures console
    channel output, and exposes ``run``/``save``/``load``/``elems``/``ops``.

    The backend is a hard dependency. If the meerk40t package is not importable
    this raises ``RuntimeError`` with install instructions.
    """

    def __init__(
        self,
        profile: str = "MeerK40t_CLI",
        ignore_settings: bool = True,
        device: str = "dummy",
        port: Optional[str] = None,
        baud: int = 115200,
    ):
        self.profile = profile
        self.ignore_settings = ignore_settings
        self.device_type = device
        self.port = port
        self.baud = baud
        self._kernel: Optional[Any] = None
        self._lock = threading.RLock()
        self._captured: list[str] = []
        self._watcher_installed = False

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Boot the headless kernel. Idempotent."""
        with self._lock:
            if self._kernel is not None:
                return
            try:
                from meerk40t.kernel import Kernel
            except ImportError as exc:  # pragma: no cover - install guard
                raise RuntimeError(
                    "MeerK40t is not installed. Install it with: "
                    "pip install -e . (from the meerk40t source tree) or "
                    "pip install meerk40t"
                ) from exc

            kernel = Kernel(
                "MeerK40t",
                "0.0.0-cli",
                self.profile,
                ansi=False,
                ignore_settings=self.ignore_settings,
            )

            # Same plugin set as test/bootstrap.py (headless, no GUI).
            from meerk40t.network import kernelserver
            kernel.add_plugin(kernelserver.plugin)
            from meerk40t.device import dummydevice
            kernel.add_plugin(dummydevice.plugin)
            from meerk40t.core import core
            kernel.add_plugin(core.plugin)
            from meerk40t.image import imagetools
            kernel.add_plugin(imagetools.plugin)
            from meerk40t.fill import fills
            kernel.add_plugin(fills.plugin)
            from meerk40t.extra.coolant import plugin as coolantplugin
            kernel.add_plugin(coolantplugin)
            from meerk40t.lihuiyu import plugin as lhystudiosdevice
            kernel.add_plugin(lhystudiosdevice.plugin)
            from meerk40t.moshi import plugin as moshidevice
            kernel.add_plugin(moshidevice.plugin)
            from meerk40t.grbl import plugin as grbldevice
            kernel.add_plugin(grbldevice.plugin)
            from meerk40t.ruida import plugin as ruidadevice
            kernel.add_plugin(ruidadevice.plugin)
            from meerk40t.newly import plugin as newlydevice
            kernel.add_plugin(newlydevice.plugin)
            from meerk40t.balormk import plugin as balormkdevice
            kernel.add_plugin(balormkdevice.plugin)
            from meerk40t.core import svg_io
            kernel.add_plugin(svg_io.plugin)
            from meerk40t.dxf.plugin import plugin as dxf_io_plugin
            kernel.add_plugin(dxf_io_plugin)
            from meerk40t.rotary import rotary
            kernel.add_plugin(rotary.plugin)

            # The bridge plugin back-fills the meerk40t/meerk40t#3249 fixes
            # (typed console `set`, set feedback, console-server handover).
            # Real `meerk40t` launches load it via the meerk40t.extension
            # entry point; this fixed plugin list must add it explicitly.
            from cli_anything.meerk40t import mk_plugin
            kernel.add_plugin(mk_plugin.plugin)

            kernel(partial=True)
            kernel.console("channel print console\n")

            if self.device_type and self.device_type != "dummy":
                provider = _device_provider_name(self.device_type)
                kernel.console(f"service device start -i {provider} 0\n")
                dev = kernel.device
                try:
                    self._apply_serial_config(dev)
                except Exception as exc:
                    # Roll back the partially booted kernel so the backend
                    # remains restartable after a bad port/baud value.
                    try:
                        kernel()
                    except Exception as teardown_exc:
                        raise RuntimeError(
                            f"serial config failed and kernel teardown also failed: {teardown_exc}"
                        ) from exc
                    raise
            else:
                kernel.console("service device start dummy 0\n")
            # Register the base-device console commands (device, devinfo, activate,
            # ...). This must run AFTER a device is active: basedevice's boot
            # lifecycle auto-starts its `preferred_device` (lhystudios) when
            # `kernel.device` is still unset, which would hijack the default.
            from meerk40t.device import basedevice
            basedevice.plugin(kernel, "boot")

            # Capture console channel output.
            kernel._console_channel.watch(self._on_channel)
            self._watcher_installed = True

            self._kernel = kernel


    def _apply_serial_config(self, dev) -> None:
        """Apply configured serial port/baud to the active device.

        A setter failure (e.g. an invalid port value) is surfaced as a
        RuntimeError rather than silently swallowed, so the operator learns
        the device did not accept the configuration.
        """
        if self.port is not None and hasattr(dev, "serial_port"):
            try:
                dev.serial_port = self.port
            except Exception as exc:
                raise RuntimeError(
                    f"failed to set serial_port={self.port!r}: {exc}"
                ) from exc
        if self.baud is not None and hasattr(dev, "baud_rate"):
            try:
                dev.baud_rate = self.baud
            except Exception as exc:
                raise RuntimeError(
                    f"failed to set baud_rate={self.baud!r}: {exc}"
                ) from exc

    def shutdown(self) -> None:
        """Tear down the kernel."""
        with self._lock:
            if self._kernel is None:
                return
            if self._watcher_installed:
                try:
                    self._kernel._console_channel.unwatch(self._on_channel)
                except Exception:
                    pass
                self._watcher_installed = False
            try:
                self._kernel()
            except Exception:
                pass
            self._kernel = None

    def __enter__(self) -> "Meerk40tBackend":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # ── channel capture ────────────────────────────────────────────────

    def _on_channel(self, message: str) -> None:
        clean = _strip_ts(_strip_ansi(str(message)))
        self._captured.append(clean)

    def reset_capture(self) -> None:
        self._captured = []

    @property
    def captured(self) -> list[str]:
        return list(self._captured)

    # ── command execution ──────────────────────────────────────────────

    @property
    def kernel(self) -> Any:
        if self._kernel is None:
            raise RuntimeError("Backend is not started. Call start() first.")
        return self._kernel

    @property
    def elements(self) -> Any:
        return self.kernel.elements

    def run(self, command: str, capture: bool = True) -> list[str]:
        """Execute a console command (or pipeline separated by ``|``).

        Returns the list of captured console-channel lines produced by the
        command. When ``capture`` is False the command still runs but the
        captured buffer is reset first and returned empty.
        """
        with self._lock:
            if capture:
                self.reset_capture()
            if not command.endswith("\n"):
                command = command + "\n"
            self.kernel.console(command)
            return self.captured

    def run_quiet(self, command: str) -> list[str]:
        """Run a command with the leading-echo line filtered out."""
        out = self.run(command)
        return [line for line in out if not _strip_ts(line).strip() == command.strip()]

    # ── file I/O (real backend) ─────────────────────────────────────────

    def save_svg(self, path: str, version: str = "default") -> bool:
        """Save the current elements tree to SVG via the real SVGWriter.

        Writes to a same-directory temporary file, verifies the artifact exists,
        is non-empty and parses as a well-formed SVG (or valid svgz), then
        atomically replaces the target. A verification failure raises
        SaveVerificationError and never touches an existing target file.
        """
        if not path.lower().endswith((".svg", ".svgz")):
            raise ValueError("save_svg requires a .svg or .svgz path")
        if version not in ("default", "plain", "compressed"):
            raise SaveVerificationError(f"unsupported version: {version!r}")
        abspath = os.path.realpath(path)
        compressed = version == "compressed" or abspath.lower().endswith(".svgz")
        # MeerK40t registers the SVGZ saver under the "compressed" version and
        # dispatches on version, so a default .svgz save must request it
        # explicitly or no saver matches and nothing is written.
        effective = "compressed" if (compressed and version == "default") else version
        suffix = ".svgz" if compressed else ".svg"
        token = secrets.token_hex(6)
        tmp = f"{abspath}.tmp-{token}{suffix}"
        cmd = f"save {tmp}"
        if effective != "default":
            cmd += f" -v {effective}"
        try:
            self.run(cmd)
            if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
                raise SaveVerificationError("backend produced no output", path=abspath)
            if compressed:
                try:
                    import gzip
                    with gzip.open(tmp, "rb") as g:
                        g.read(1)
                except Exception as exc:
                    raise SaveVerificationError(f"invalid svgz: {exc}", path=abspath)
            else:
                try:
                    tree = ET.parse(tmp)
                    root_tag = tree.getroot().tag
                    local = root_tag.rsplit("}", 1)[-1].lower()
                    if local != "svg":
                        raise SaveVerificationError(
                            f"output root is {root_tag!r}, not SVG", path=abspath
                        )
                except SaveVerificationError:
                    raise
                except Exception as exc:
                    raise SaveVerificationError(f"output is not well-formed SVG: {exc}", path=abspath)
            os.replace(tmp, abspath)
        except BaseException:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise
        return True

    def load_file(self, path: str) -> bool:
        """Load an SVG/DXF file into the elements tree via the real loader.

        Raises FileNotFoundError when the path is missing and LoadError when the
        backend reports a load failure (inspected from captured console output).
        """
        abspath = os.path.realpath(path)
        if not os.path.exists(abspath):
            raise FileNotFoundError(abspath)
        self.run(f"load {abspath}")
        errors = [line for line in self.captured if _looks_like_error(line)]
        if errors:
            raise LoadError("; ".join(errors), path=abspath)
        return True

    # ── introspection ──────────────────────────────────────────────────

    def elems(self) -> list[Any]:
        """Return all element nodes in the elements tree."""
        return list(self.elements.elems())

    def ops(self) -> list[Any]:
        """Return all operation nodes in the elements tree."""
        return list(self.elements.ops())

    def elem_count(self) -> int:
        return len(self.elems())

    def op_count(self) -> int:
        return len(self.ops())

    def device(self) -> Any:
        """Return the active device service (or None)."""
        try:
            return self.kernel.device
        except Exception:
            return None

    def has_command(self, command: str) -> bool:
        return bool(self.kernel.has_command(command))

    def help_text(self, command: Optional[str] = None) -> str:
        if command:
            out = self.run(f"help {command}")
        else:
            out = self.run("help")
        return "\n".join(out)