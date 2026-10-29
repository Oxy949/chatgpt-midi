from pathlib import Path
import tempfile
import unittest

from midivoice.resources import browser_pss_kb, process_tree


class MemoryTests(unittest.TestCase):
    def test_descendants_without_kernel_children_files_are_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid, parent, pss in (
                (10, 1, 100),
                (20, 10, 200),
                (30, 20, 300),
                (40, 1, 400),
            ):
                folder = root / str(pid)
                folder.mkdir()
                (folder / "status").write_text(f"Name:\tchromium\nPPid:\t{parent}\n")
                (folder / "smaps_rollup").write_text(
                    f"Pss: {pss} kB\n", encoding="utf-8"
                )
            self.assertEqual(process_tree(10, root), {10, 20, 30})
            self.assertEqual(browser_pss_kb(10, root), 600)

    def test_incomplete_proc_entries_do_not_break_the_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid, status in ((20, "PPid: 10"), (30, "PPid: invalid"), (40, "")):
                folder = root / str(pid)
                folder.mkdir()
                (folder / "status").write_text(status)
            (root / "50").mkdir()  # A process disappeared before status was read.
            self.assertEqual(process_tree(10, root), {10, 20})

    def test_missing_process_has_unknown_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(browser_pss_kb(123, Path(directory)))
