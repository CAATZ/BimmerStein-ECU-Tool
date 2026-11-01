"""Recorder compatibility contract shared by pytest and Android's build gate."""

import unittest

import ms413_diag
from engines.patcher import patch_ms41


class CurrentRecorderCompatibilityTest(unittest.TestCase):
    def test_recorder_accepts_current_ms413_patch_pair(self):
        patches = patch_ms41.load_patches()
        current = {}
        for feature in ("ignition_cut", "launch_control"):
            candidates = [
                patch for name, patch in patches.items()
                if name.startswith(feature + "_")
                and patch.get("target") == "MS41.3"
                and not patch.get("deprecated")
            ]
            self.assertEqual(len(candidates), 1, feature)
            current[feature] = candidates[0]
        ignition = current["ignition_cut"]
        launch = current["launch_control"]
        self.assertIn(ignition["id"], launch["requires"])

        # Exact descriptor payloads suffice for probe admission; no private ROM
        # fixture, firmware execution or ECU connection is needed by this gate.
        image = bytearray(b"\xff" * 0x40000)
        for patch in (ignition, launch):
            for edit in patch["edits"]:
                data = bytes.fromhex(edit["data"])
                image[edit["off"]:edit["off"] + len(data)] = data

        class ImageDS2:
            def read_mem(self, address, length):
                return bytes(image[(address + index) ^ 0x4000]
                             for index in range(length))

        probes = ms413_diag._patch_probe_snapshot(ImageDS2())
        self.assertEqual(ms413_diag._runtime_revision(probes), ignition["version"])
        self.assertEqual(ms413_diag._required_probe_failures(probes), [])
        for patch in (ignition, launch):
            names = [name for name in probes if name.startswith(patch["id"] + "_")]
            self.assertTrue(names, patch["id"])
            for name in names:
                with self.subTest(probe=name):
                    self.assertTrue(probes[name]["matches"])
                    probes[name]["matches"] = False
                    self.assertTrue(ms413_diag._required_probe_failures(probes))
                    probes[name]["matches"] = True


if __name__ == "__main__":
    unittest.main()
