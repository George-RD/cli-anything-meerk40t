"""Unit tests for the cli_anything MeerK40t bridge plugin.

The plugin back-fills three upstream fixes from MeerK40t PR #3249 into a
stock PyPI install that lacks them. These tests verify:

* the plugin registers through the ``meerk40t.extension`` entry point,
* each fix is detected and applied only when genuinely missing,
* the fixes are idempotent and safe when already present,
* a single failing patch never stops the others or raises into boot,
* the source transforms are faithful to the upstream diff.

No serial ports are opened. Every kernel is booted headlessly.
"""

from __future__ import annotations

import inspect
import os
import tempfile
import unittest

from meerk40t.kernel import Kernel
from meerk40t.svgelements import Length

import cli_anything.meerk40t.mk_plugin as mk


_BROKEN_CS_PLUGIN_SRC = """def plugin(kernel, lifecycle=None):
    if lifecycle == "register":
        root = kernel.root
        def server_console():
            def exec_command(data):
                while True:
                    if handover is None:
                        root.console(data)
                    else:
                        handover(data)
                    break
            handover = None
            for result in root.find("gui/handover"):
                # Do we have a thread handover routine?
                if result is not None:
                    handover, _path, suffix_path = result
                    break
            recv = None
            recv.watch(exec_command)
        server_console()
"""


def boot_kernel():
    """Boot a headless kernel with the dummy device and the bridge plugin."""
    kernel = Kernel(
        "MeerK40t", "0.0.0", "testprof", ansi=False, ignore_settings=True
    )
    from meerk40t.device import dummydevice

    kernel.add_plugin(dummydevice.plugin)
    kernel.add_plugin(mk.plugin)
    kernel(partial=True)
    return kernel


class BridgePluginTest(unittest.TestCase):
    def setUp(self):
        # Preserve the live console/web server modules so tests that mutate
        # them can be restored afterwards.
        import meerk40t.network.console_server as cs

        self._orig_cs_plugin = cs.plugin
        self._orig_cs_marker = getattr(cs, "_cli_anything_patched_plugin", False)

    def tearDown(self):
        import meerk40t.network.console_server as cs

        cs.plugin = self._orig_cs_plugin
        if self._orig_cs_marker:
            cs._cli_anything_patched_plugin = True
        else:
            if hasattr(cs, "_cli_anything_patched_plugin"):
                del cs._cli_anything_patched_plugin

    # -- entry point --------------------------------------------------------

    def test_entry_point_registers_plugin(self):
        kernel = boot_kernel()
        self.assertIn(mk.plugin, kernel._kernel_plugins)
        self.assertTrue(getattr(kernel, "_cli_anything_mk_loaded", False))

    # -- version fast-path --------------------------------------------------

    def test_version_fastpath_skips_when_fixed(self):
        original = mk._meerk40t_version

        def fake_version():
            return "0.9.9001"

        mk._meerk40t_version = fake_version
        try:
            kernel = boot_kernel()
        finally:
            mk._meerk40t_version = original
        status = getattr(kernel, "_cli_anything_mk_patches", {})
        self.assertEqual(
            status.get(mk.PATCH_HANDOVER), "skipped-already-fixed"
        )
        self.assertEqual(status.get(mk.PATCH_TYPED), "skipped-already-fixed")
        self.assertEqual(status.get(mk.PATCH_FEEDBACK), "skipped-already-fixed")

    # -- transform purity (faithful to upstream diff) -----------------------

    def test_transform_console_server_broken_to_fixed(self):
        fixed = mk._transform_console_server_source(_BROKEN_CS_PLUGIN_SRC)
        self.assertNotEqual(fixed, _BROKEN_CS_PLUGIN_SRC)
        self.assertIn("def find_handover():", fixed)
        self.assertIn('root.lookup("gui/handover")', fixed)
        self.assertIn("handover = find_handover()", fixed)

    def test_transform_console_server_idempotent(self):
        fixed = mk._transform_console_server_source(_BROKEN_CS_PLUGIN_SRC)
        self.assertEqual(mk._transform_console_server_source(fixed), fixed)

    def test_transform_web_server_broken_to_fixed(self):
        broken = (
            '    def send_command(self, command: str) -> None:\n'
            '        if self.handover is None:\n'
            '            self.context(f"{command}\\n")\n'
            "        else:\n"
            "            self.handover(command)\n"
        )
        fixed = mk._transform_web_server_send_command(broken)
        self.assertNotEqual(fixed, broken)
        self.assertIn('self.handover = self.context.root.lookup("gui/handover")', fixed)

    def test_transform_web_server_idempotent(self):
        broken = (
            '    def send_command(self, command: str) -> None:\n'
            '        if self.handover is None:\n'
            '            self.handover = self.context.root.lookup("gui/handover")\n'
            '            self.context(f"{command}\\n")\n'
            "        else:\n"
            "            self.handover(command)\n"
        )
        self.assertEqual(mk._transform_web_server_send_command(broken), broken)

    # -- runtime handover patch --------------------------------------------

    def test_handover_runtime_patch_applies(self):
        import meerk40t.network.console_server as cs

        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False
        ) as tmp:
            tmp.write(_BROKEN_CS_PLUGIN_SRC)
            tmp_path = tmp.name
        try:
            namespace = {}
            with open(tmp_path) as handle:
                source = handle.read()
            exec(compile(source, tmp_path, "exec"), namespace)
            cs.plugin = namespace["plugin"]
            if hasattr(cs, "_cli_anything_patched_plugin"):
                del cs._cli_anything_patched_plugin

            # boot_kernel runs the bridge at the boot lifecycle, which must
            # detect the broken handover and re-bind the module function.
            kernel = boot_kernel()
            status = getattr(kernel, "_cli_anything_mk_patches", {})
            self.assertEqual(status.get(mk.PATCH_HANDOVER), "applied")
            # The module function must have been rebound to a fresh object.
            self.assertIsNot(cs.plugin, namespace["plugin"])
        finally:
            os.unlink(tmp_path)

    def _downgrade_set(self, kernel):
        """Replace the live ``set`` command with a broken, untyped version."""

        def broken_set(channel, _, path=None, args=tuple(), **kwargs):
            rc = kernel.get_context(path) if path is not None else kernel.root
            if len(args) >= 2:
                attr = args[0]
                value = args[1]
                if hasattr(rc, attr):
                    v = getattr(rc, attr)
                    if isinstance(v, (bool, int, float, str)):
                        setattr(
                            rc,
                            attr,
                            v if not isinstance(v, bool) else (value.lower() == "true"),
                        )
                    else:
                        # Broken: stores the bare string, drops the type.
                        setattr(rc, attr, value)
                else:
                    channel(f"No such attribute: {attr}")
            return

        kernel.console_command_remove("set")
        kernel.console_command("set", help="set")(broken_set)
    def test_backfill_fixes_broken_set_typed_and_feedback(self):
        kernel = boot_kernel()
        root = kernel.root
        root.bridge_len = Length("0mm")
        self._downgrade_set(kernel)
        # The broken command stores a string, not a Length.
        kernel.console("set bridge_len 10mm\n")
        self.assertEqual(root.bridge_len, "10mm")

        # Reset the per-patch status and re-apply the bridge fixes.
        del kernel._cli_anything_mk_patches
        captured = []
        kernel.channel("console").watch(
            lambda *a, **kw: captured.append(" ".join(str(x) for x in a))
        )
        mk.apply_backfill_patches(kernel)
        status = getattr(kernel, "_cli_anything_mk_patches", {})
        self.assertEqual(status.get(mk.PATCH_TYPED), "applied")
        self.assertEqual(status.get(mk.PATCH_FEEDBACK), "applied")

        # Now the typed value is reconstructed and feedback is emitted.
        root.bridge_len = Length("0mm")
        kernel.console("set bridge_len 10mm\n")
        self.assertIsInstance(root.bridge_len, Length)
        self.assertEqual(root.bridge_len, Length("10mm"))
        self.assertTrue(any("bridge_len" in c and "10mm" in c for c in captured))

    def test_backfill_idempotent_on_broken_set(self):
        kernel = boot_kernel()
        root = kernel.root
        root.idem_len = Length("0mm")
        self._downgrade_set(kernel)
        del kernel._cli_anything_mk_patches
        mk.apply_backfill_patches(kernel)
        # Apply a second time; must remain safe and still typed.
        mk.apply_backfill_patches(kernel)
        root.idem_len = Length("0mm")
        kernel.console("set idem_len 10mm\n")
        self.assertIsInstance(root.idem_len, Length)
        self.assertEqual(root.idem_len, Length("10mm"))

    def test_already_fixed_set_is_noop(self):
        kernel = boot_kernel()
        root = kernel.root
        root.fixed_len = Length("0mm")
        # The dev install already carries the fixes; apply must no-op.
        mk.apply_backfill_patches(kernel)
        status = getattr(kernel, "_cli_anything_mk_patches", {})
        self.assertEqual(status.get(mk.PATCH_TYPED), "skipped-already-fixed")
        self.assertEqual(status.get(mk.PATCH_FEEDBACK), "skipped-already-fixed")
        # Setting a Length still works through the untouched fixed command.
        root.fixed_len = Length("0mm")
        kernel.console("set fixed_len 10mm\n")
        self.assertIsInstance(root.fixed_len, Length)

    # -- failure isolation -------------------------------------------------

    def test_patch_failure_is_isolated_and_reported(self):
        kernel = boot_kernel()
        root = kernel.root
        root.iso_len = Length("0mm")
        self._downgrade_set(kernel)
        del kernel._cli_anything_mk_patches

        real_register = mk._register_fixed_set

        def boom(k):
            raise RuntimeError("simulated registration failure")

        mk._register_fixed_set = boom
        try:
            # apply_backfill_patches must not raise despite the set patch
            # failing; it must report the skip on the channel.
            captured = []
            kernel.channel(mk._CHANNEL_NAME).watch(
                lambda *a, **kw: captured.append(" ".join(str(x) for x in a))
            )
            mk.apply_backfill_patches(kernel)
        finally:
            mk._register_fixed_set = real_register

        status = getattr(kernel, "_cli_anything_mk_patches", {})
        self.assertEqual(status.get(mk.PATCH_TYPED), "failed")
        self.assertTrue(any(mk.UPSTREAM_PR in c for c in captured))

    def test_plugin_never_raises_into_boot(self):
        kernel = boot_kernel()

        def boom(k):
            raise RuntimeError("apply must never escape")

        real_apply = mk.apply_backfill_patches
        mk.apply_backfill_patches = boom
        try:
            # The plugin swallows any error from apply_backfill_patches.
            self.assertIsNone(mk.plugin(kernel, "boot"))
        finally:
            mk.apply_backfill_patches = real_apply


if __name__ == "__main__":
    unittest.main()
