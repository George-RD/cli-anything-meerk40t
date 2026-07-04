import os

from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend


def export_svg(backend, path, version="default"):
    backend.save_svg(path, version)
    return {
        "output": path,
        "format": "svg",
        "size_bytes": os.path.getsize(path),
        "version": version,
    }


def export_svgz(backend, path):
    return export_svg(backend, path, version="compressed")


def export_png(backend, path, dpi=300):
    renderer = backend.kernel.lookup("render-op/make_raster")
    if renderer is None:
        raise RuntimeError(
            "PNG export requires wxPython GUI (render-op/make_raster not registered in headless mode). "
            "Install wxPython and run with a display, or use SVG export instead."
        )
    backend.run(f"element* render -d {dpi}")
    raise RuntimeError(
        "PNG export to file path is not implemented for this renderer. "
        "Use SVG export or run inside the GUI."
    )


def export_gcode(backend, path):
    """Best-effort G-code export via the GRBL plan/save_job pipeline.

    Requires an active GRBL device. Uses the kernel's
    `plan copy preprocess validate blob save_job <path>` pipeline, which the
    GRBL plugin uses internally to write real G-code to a file. If the
    pipeline is unavailable or produces no G-code (common in headless or
    non-GRBL setups), this raises a RuntimeError with guidance.
    """
    dev = backend.device()
    dev_name = str(dev).lower() if dev else ""
    if "grbl" not in dev_name:
        raise RuntimeError(
            "G-code export requires an active GRBL device. "
            "Activate one via the console passthrough, then retry. "
            "G-code emission requires an active device + spooler execution."
        )
    abspath = os.path.realpath(path)
    backend.run(f"plan copy preprocess validate blob save_job {abspath}")
    if not os.path.exists(abspath) or os.path.getsize(abspath) == 0:
        raise RuntimeError(
            "G-code export produced no output. "
            "Ensure there are classified operations with elements and the GRBL device is active. "
            "Use the console passthrough for full control over the active device and spooler."
        )
    with open(abspath, "r", encoding="utf-8", errors="replace") as f:
        sample = f.read(2048)
    has_gcode = any(
        line.strip().startswith(("G", "M", "g", "m"))
        for line in sample.splitlines()
    )
    if not has_gcode:
        raise RuntimeError(
            "G-code export produced invalid output (no G/M codes found). "
            "The file may contain log lines instead of real G-code. "
            "Use the console passthrough for full control."
        )
    return {
        "output": abspath,
        "format": "gcode",
        "size_bytes": os.path.getsize(abspath),
        "valid": True,
    }
