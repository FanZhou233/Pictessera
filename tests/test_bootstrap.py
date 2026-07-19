import os
import unittest
from unittest.mock import patch

from photo_manager.bootstrap import configure_qt_environment


class BootstrapTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows-specific Qt platform policy")
    def test_windows_preserves_established_freetype_rendering_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QT_QPA_PLATFORM", None)
            configure_qt_environment()
            self.assertEqual(os.environ["QT_QPA_PLATFORM"], "windows:fontengine=freetype")

    @unittest.skipUnless(os.name == "nt", "Windows-specific Qt platform policy")
    def test_explicit_qt_platform_is_preserved(self):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}, clear=False):
            configure_qt_environment()
            self.assertEqual(os.environ["QT_QPA_PLATFORM"], "offscreen")


if __name__ == "__main__":
    unittest.main()
