"""Kernel-side control commands for the cli-anything MeerK40t agent.

Exposes a framed, single-line JSON protocol on the console channel so the
CLI attach client can drive a running MeerK40t GUI/kernel headlessly without
parsing prose or relying on command ordering.

Commands carried by the versioned envelope (see ``attach_envelope``):
  status       - device/bed/element/op status; the reply echoes the request's
                 ``request_id`` and ``v``.
  stage        - load the job SVG carried inside the envelope bytes, after a
                 sha256 integrity check against the manifest. The receiver
                 never receives, reads, or interpolates a filesystem path.

Requests arrive as a single base64 envelope token; every reply is one
``#CLIA1#`` frame that echoes the request's ``request_id`` and ``v``.
"""
from __future__ import annotations
from cli_anything.meerk40t.utils.manifest import (
    strict_load_json,
    ManifestValidationError,
)
from cli_anything.meerk40t.utils.job_preflight import verify_job_artefacts
from cli_anything.meerk40t.utils.profiles import load_profile
from cli_anything.meerk40t.utils.job_prep import _dim_mm
from meerk40t.core.units import UNITS_PER_MM

import json
import os
import hashlib
import tempfile
import threading
from cli_anything.meerk40t.utils import serial_probe
from cli_anything.meerk40t.utils.attach_envelope import (
    PROTOCOL_VERSION,
    decode_request,
    format_reply,
    AttachEnvelopeError,
)


# Module-level guard used to create exactly one staging lock per kernel the
# first time staging is attempted, independent of registration/plugin lifecycle.
_stage_lock_bootstrap = threading.Lock()
# Fallback used only if a kernel refuses attribute assignment; at least this
# serializes every such degenerate caller instead of creating a no-op lock.
_stage_lock_fallback = threading.Lock()


def _get_stage_lock(kernel):
    """Return the per-kernel staging lock, creating it once if absent.

    Registration eagerly creates the lock as an optimization, but staging must
    not depend on that path: this guards the creation so concurrent callers
    cannot each build their own lock (which would defeat serialization).
    """
    lock = getattr(kernel, "_cli_anything_stage_lock", None)
    if lock is not None:
        return lock
    with _stage_lock_bootstrap:
        lock = getattr(kernel, "_cli_anything_stage_lock", None)
        if lock is None:
            lock = threading.Lock()
            try:
                kernel._cli_anything_stage_lock = lock
            except Exception:
                return _stage_lock_fallback
    return lock

def register(kernel):
    """Register the ``agent`` console command and its subcommands.

    Idempotent: safe to call multiple times across kernel lifecycles.
    """
    # Per-kernel lock serializing the staging critical section so concurrent
    # stage requests cannot interleave their load/verify/commit and corrupt the
    # shared kernel scene's inventory accounting. Created once (lazily) and
    # reused; using the shared helper keeps registration and first-staging from
    # creating two different locks under a race.
    _get_stage_lock(kernel)
    if getattr(kernel, "_cli_anything_mk_control", False):
        return
    _register_agent_command(kernel)
    kernel._cli_anything_mk_control = True


def _register_agent_command(kernel):
    def _agent_command(channel, _, args=tuple(), **kwargs):
        if not args:
            channel(format_reply(None, error="usage: agent <envelope-token>"))
            return
        try:
            req = decode_request(args[0])
        except AttachEnvelopeError as exc:
            channel(format_reply(None, error=str(exc)))
            return
        request_id = req.get("request_id")
        cmd = req.get("cmd")
        if req.get("v") != PROTOCOL_VERSION:
            channel(
                format_reply(
                    request_id,
                    error=(
                        f"unsupported protocol version {req.get('v')} "
                        f"— expected {PROTOCOL_VERSION}"
                    ),
                )
            )
            return
        if cmd == "status":
            channel(format_reply(request_id, **_build_status(kernel)))
        elif cmd == "stage":
            try:
                payload = _stage_file(
                    kernel,
                    req.get("svg"),
                    req.get("manifest"),
                    gcode_bytes=req.get("gcode"),
                    allow_estimated=req.get("allow_estimated", False),
                )
            except Exception as exc:  # noqa: BLE001
                payload = {"error": str(exc)}
            channel(format_reply(request_id, **payload))
        else:
            channel(format_reply(request_id, error=f"unknown envelope command: {cmd!r}"))

    kernel.console_command(
        "agent",
        help="CLI Anything agent control commands.",
    )(_agent_command)




def _build_status(kernel):
    device = getattr(kernel, "device", None)
    elements = getattr(kernel, "elements", None)

    elem_count = 0
    op_count = 0
    if elements is not None:
        try:
            elem_count = len(list(elements.elems()))
        except Exception:
            pass
        try:
            op_count = len(list(elements.ops()))
        except Exception:
            pass

    device_label = None
    devices = []
    serial_port = None
    grbl_state = "unknown"
    bed = {"width": None, "height": None}
    spooler_queue = 0

    if device is not None:
        device_label = getattr(device, "label", None) or getattr(device, "name", None)
        if device_label:
            devices = [device_label]

        if hasattr(device, "bedwidth"):
            bed["width"] = str(device.bedwidth)
        if hasattr(device, "bedheight"):
            bed["height"] = str(device.bedheight)

        spooler = getattr(device, "spooler", None)
        if spooler is not None:
            try:
                spooler_queue = len(spooler)
            except Exception:
                pass

        raw_port = getattr(device, "serial_port", None) or getattr(device, "port", None)
        if raw_port is not None and str(raw_port).lower() != "unconfigured":
            serial_port = str(raw_port)
        # Preserve the full GRBL state vocabulary (incl. Hold/Door/Check/Home)
        # instead of collapsing unknown states to "unknown".
        state = getattr(device, "_state", None)
        parsed_base, parsed_sub = serial_probe.parse_grbl_state(state or "")
        if parsed_base is not None:
            grbl_state = parsed_base if parsed_sub is None else f"{parsed_base}:{parsed_sub}"
        else:
            driver = getattr(device, "driver", None)
            if driver is not None:
                drv_state = getattr(driver, "grbl_state", None) or getattr(
                    driver, "_state", None
                )
                parsed_base, parsed_sub = serial_probe.parse_grbl_state(drv_state or "")
                if parsed_base is not None:
                    grbl_state = parsed_base if parsed_sub is None else f"{parsed_base}:{parsed_sub}"

    return {
        "protocol": PROTOCOL_VERSION,
        "devices": devices,
        "active_device": device_label,
        "serial_port": serial_port,
        "grbl_state": grbl_state,
        "bed": bed,
        "elements": elem_count,
        "operations": op_count,
        "spooler_queue": spooler_queue,
    }



_KIND_NORMALIZE = {"engrave": "engrave", "cut": "cut", "etch": "engrave", "score": "engrave"}
_BOUNDS_TOL_MM = 1.0
_BED_TOL_MM = 1.0


def _all_nodes(elements):
    return list(elements.ops()) + list(elements.elems())


def _remove_nodes(nodes):
    """Remove every node, returning the list of nodes whose removal raised."""
    failed = []
    for node in nodes:
        try:
            node.remove_node()
        except Exception:
            try:
                node.remove()
            except Exception:
                failed.append(node)
    return failed


def _commit_replacement(elements, added_ids):
    """Detach every pre-existing node, reversibly, leaving only the staged job.

    Old nodes are removed with ``destroy=False`` and with ``children``/``references``
    left intact, while their parent and child index are recorded. If any removal
    raises, the already-detached subtrees are re-attached (in the SAME forward
    order they were detached) so the original sibling ordering is restored
    exactly. Returns an error string on failure, else ``None``.
    """
    old = []
    for n in _all_nodes(elements):
        if id(n) in added_ids:
            continue
        parent = n.parent
        idx = None
        if parent is not None:
            try:
                idx = parent.children.index(n)
            except (ValueError, AttributeError):
                idx = None
        old.append((n, parent, idx))
    detached = []
    for n, parent, idx in old:
        try:
            n.remove_node(children=False, references=False, destroy=False)
            detached.append((n, parent, idx))
        except Exception:
            restore_failed = 0
            for rn, rp, ri in detached:
                if rp is not None and ri is not None:
                    try:
                        rp.add_node(rn, pos=ri)
                    except Exception:
                        restore_failed += 1
            if restore_failed:
                return "scene commit failed; rollback incomplete"
            return "scene commit failed; staged job rolled back"
    return None


def _op_summary(op):
    op_type = getattr(op, "type", "") or ""
    kind = op_type.split()[-1] if op_type else "unknown"
    try:
        op_elems = len(getattr(op, "children", []))
    except Exception:
        op_elems = 0
    return {
        "kind": kind,
        "power": getattr(op, "power", None),
        "speed": getattr(op, "speed", None),
        "passes": getattr(op, "passes", None),
        "elements": op_elems,
    }


def _norm_kind(kind):
    return _KIND_NORMALIZE.get((kind or "").lower(), "unknown")


def _op_inventory_key(op):
    """(normalized_kind, color_lower, element_count) for an added operation node."""
    color = str(getattr(op, "color", None) or "").lower()
    kind = _norm_kind(getattr(op, "type", "").split()[-1] if getattr(op, "type", "") else "unknown")
    try:
        elems = len(getattr(op, "children", []))
    except Exception:
        elems = 0
    return (kind, color, elems)


def _check_inventory(added_ops, added_elems, manifest):
    """Return an error string if the staged scene disagrees with the manifest."""
    man_ops = manifest.get("operations", []) or []
    man_total_elems = sum(max(0, int(o.get("elements", 0))) for o in man_ops)
    if len(added_elems) != man_total_elems:
        return (
            f"inventory mismatch: staged {len(added_elems)} elements but manifest "
            f"declares {man_total_elems}"
        )
    if len(added_ops) != len(man_ops):
        return (
            f"inventory mismatch: staged {len(added_ops)} operations but manifest "
            f"declares {len(man_ops)}"
        )
    candidate: dict[tuple, int] = {}
    for op in added_ops:
        key = _op_inventory_key(op)
        candidate[key] = candidate.get(key, 0) + 1
    manifest_set: dict[tuple, int] = {}
    for o in man_ops:
        key = (_norm_kind(o.get("kind")), (o.get("color") or "").lower(), int(o.get("elements", 0)))
        manifest_set[key] = manifest_set.get(key, 0) + 1
    if candidate != manifest_set:
        return (
            "inventory mismatch: staged operation set (kind/color/element-count) "
            "differs from manifest"
        )
    return None


def _check_machine_binding(kernel, manifest):
    """Refuse to stage a job whose machine profile does not match the live device."""
    machine = manifest.get("machine")
    if not machine:
        return "manifest missing machine - cannot bind to live device"
    profile = load_profile(machine)
    if profile is None:
        return f"unknown machine profile {machine!r} - cannot bind to live device"
    device = getattr(kernel, "device", None)
    if device is None:
        return "no live device available - cannot bind"
    expected_provider = (profile.get("device") or "").lower()
    if expected_provider:
        mod = type(device).__module__.lower()
        if expected_provider not in mod:
            return (
                f"machine binding refused: manifest targets {expected_provider!r} "
                f"but live device is {type(device).__name__!r}"
            )
    try:
        live_w = _dim_mm(getattr(device, "bedwidth", None))
        live_h = _dim_mm(getattr(device, "bedheight", None))
    except Exception:
        return "live device bed dimensions unavailable - cannot bind"
    prof_w = _dim_mm(profile.get("bedwidth"))
    prof_h = _dim_mm(profile.get("bedheight"))
    if abs(live_w - prof_w) > _BED_TOL_MM or abs(live_h - prof_h) > _BED_TOL_MM:
        return (
            f"machine binding refused: manifest bed {prof_w:.0f}x{prof_h:.0f}mm "
            f"does not match live bed {live_w:.0f}x{live_h:.0f}mm"
        )
    return None


def _bbox_mm(elem):
    """Return (xmin, ymin, xmax, ymax) in mm for an element, or None if no bounds."""
    try:
        bb = elem.bbox()
    except Exception:
        bb = None
    if not bb:
        return None
    x1, y1, x2, y2 = (float(v) / UNITS_PER_MM for v in bb)
    return (x1, y1, x2, y2)


def _check_bounds(kernel, added_elems):
    """Refuse if any staged geometry falls outside the live bed (0..bed)."""
    device = getattr(kernel, "device", None)
    if device is None:
        return None
    try:
        bw = _dim_mm(getattr(device, "bedwidth", None))
        bh = _dim_mm(getattr(device, "bedheight", None))
    except Exception:
        return None
    xmin = ymin = xmax = ymax = None
    for elem in added_elems:
        bb = _bbox_mm(elem)
        if bb is None:
            continue
        a, c, d, e = bb
        xmin = a if xmin is None else min(xmin, a)
        xmax = d if xmax is None else max(xmax, d)
        ymin = c if ymin is None else min(ymin, c)
        ymax = e if ymax is None else max(ymax, e)
    if xmin is None:
        return None
    if (
        xmin < -_BOUNDS_TOL_MM
        or ymin < -_BOUNDS_TOL_MM
        or xmax > bw + _BOUNDS_TOL_MM
        or ymax > bh + _BOUNDS_TOL_MM
    ):
        return (
            f"staged geometry exceeds live bed {bw:.0f}x{bh:.0f}mm "
            f"(extents {xmin:.1f},{ymin:.1f}..{xmax:.1f},{ymax:.1f}mm)"
        )
    return None


def _stage_file(kernel, svg_bytes, manifest_bytes, allow_estimated=False, gcode_bytes=None):
    """Public entry: serialize the staging critical section per kernel.

    Concurrent stage requests on a shared kernel scene must not interleave
    their load/verify/commit, or the inventory accounting (which derives added
    nodes from the live scene) becomes inconsistent. The lock is created once
    per kernel (lazily, under a module-level bootstrap guard), so staging does
    not depend on the registration/plugin lifecycle. Callers without a kernel
    fall back to a shared module-level lock.
    """
    lock = _get_stage_lock(kernel)
    if lock is not None:
        lock.acquire()
    try:
        return _stage_file_impl(kernel, svg_bytes, manifest_bytes, allow_estimated, gcode_bytes)
    finally:
        if lock is not None:
            lock.release()


def _stage_file_impl(kernel, svg_bytes, manifest_bytes, allow_estimated=False, gcode_bytes=None):
    # The CLI sends the job SVG, G-code, and manifest as raw envelope bytes; the
    # receiver never receives, reads, or interpolates a filesystem path.
    # Integrity is verified against the manifest's recorded sha256, G-code
    # modal-safety (recomputed from the carried bytes), machine-binding,
    # inventory, and live-bed bounds BEFORE the live scene is committed. Any
    # failed postcondition removes exactly the nodes this load added, leaving
    # the pre-existing scene untouched (transactional replacement).
    if not svg_bytes:
        raise ValueError("stage envelope missing svg bytes")
    if not manifest_bytes:
        raise ValueError("stage envelope missing manifest bytes")
    # G-code is MANDATORY for staging: the receiver must recompute modal-safety
    # from the carried bytes, never trust the manifest's stored verdict. A stage
    # envelope without g-code is refused before any scene mutation.
    if not gcode_bytes:
        return {"error": "staging requires g-code bytes (attach envelope must carry non-empty gcode)"}

    # 1. Parse strictly (rejects NaN/Inf, duplicate keys) and run the shared
    #    verifier on the received SVG bytes.
    try:
        manifest = strict_load_json(manifest_bytes.decode("utf-8"))
    except ManifestValidationError as exc:
        return {"error": "manifest validation failed: " + "; ".join(exc.errors)}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"error": f"invalid manifest JSON: {exc}"}

    result, _code = verify_job_artefacts(
        manifest,
        file_bytes={"job_svg": svg_bytes, "gcode": gcode_bytes},
        allow_estimated=allow_estimated,
        stage_mode=True,
    )
    if not result.get("ok"):
        return {"error": "; ".join(result.get("failures", ["staging refused"]))}

    # 2. Machine-binding: the live device must be the one the manifest targets.
    bind_err = _check_machine_binding(kernel, manifest)
    if bind_err is not None:
        return {"error": bind_err}

    elements = getattr(kernel, "elements", None)
    if elements is None:
        raise RuntimeError("elements service is not available")
    # Transactional guarantee (M3): the MeerK40t SVG loader is treated as
    # ADDITIVE - it appends new nodes and does not mutate pre-existing ones. We
    # track exactly the nodes this load added (added_ids). Any failure BEFORE
    # commit (loader exception, loader-returned-False, hash/inventory/bounds/
    # machine-binding/modal-safety refusal) removes only the added nodes, so the
    # pre-existing scene is left untouched. A commit failure (an old node raises
    # on detach) re-attaches the already-detached old nodes in the SAME forward
    # order they were detached, restoring the original sibling ordering. A
    # SUCCESSFUL commit intentionally removes the old scene: the staged job
    # replaces it. Pre-existing nodes are therefore never mutated in place; only
    # removed wholesale on commit or (in the failure paths) left whole.

    # Write the received bytes to a receiver-owned temp file, then load via the
    # typed loader (NEVER kernel.console string interpolation of a path).
    fd, temp_path = tempfile.mkstemp(suffix=".svg", prefix="mk_stage_")
    with os.fdopen(fd, "wb") as fh:
        fh.write(svg_bytes)
    try:
        pre_ids = {id(n) for n in _all_nodes(elements)}
        try:
            loaded = elements.load(temp_path)
        except Exception as exc:
            # Loader raised mid-load: roll back anything it already appended,
            # then surface the failure so the scene stays exactly as it was.
            added = [n for n in _all_nodes(elements) if id(n) not in pre_ids]
            _remove_nodes(added)
            raise
        added_ids = {id(n) for n in _all_nodes(elements)} - pre_ids
        added_ops = [o for o in elements.ops() if id(o) in added_ids]
        added_elems = [e for e in elements.elems() if id(e) in added_ids]

        if not loaded:
            # Loader returned false without raising: nothing was appended, but
            # be defensive and clear any partial additions.
            failed = _remove_nodes(added_ops + added_elems)
            if failed:
                return {"error": "staged SVG failed to load and rollback was incomplete"}
            return {"error": "receiver failed to load staged SVG (loader returned false)"}

        # 3. Inventory postcondition: the staged scene must match the manifest.
        inv_err = _check_inventory(added_ops, added_elems, manifest)
        if inv_err is not None:
            failed = _remove_nodes(added_ops + added_elems)
            msg = inv_err
            if failed:
                msg += "; rollback incomplete for %d node(s)" % len(failed)
            return {"error": msg}

        # 4. Geometry postcondition: staged bounds must fit the live bed.
        bounds_err = _check_bounds(kernel, added_elems)
        if bounds_err is not None:
            failed = _remove_nodes(added_ops + added_elems)
            msg = bounds_err
            if failed:
                msg += "; rollback incomplete for %d node(s)" % len(failed)
            return {"error": msg}

        # 5. Commit: detach every node that pre-dated this load (previous job,
        #    auto-default ops, etc.), leaving exactly the staged job. Detachment
        #    is reversible: if any old node fails to detach, the already-detached
        #    subtrees are re-attached and the staged job is rolled back so the
        #    original scene is left exactly as it was.
        commit_err = _commit_replacement(elements, added_ids)
        if commit_err is not None:
            added_failed = _remove_nodes(added_ops + added_elems)
            if added_failed:
                return {"error": "scene commit failed; rollback incomplete"}
            return {"error": commit_err}
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    return {
        "elements": len(added_elems),
        "operations": [_op_summary(o) for o in added_ops],
    }
