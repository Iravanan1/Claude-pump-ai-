import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure backend folder is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import tray_app

class TestTrayApp(unittest.TestCase):
    def test_create_image(self):
        image = tray_app.create_image()
        self.assertIsNotNone(image)
        self.assertEqual(image.size, (64, 64))
        
    @patch('subprocess.Popen')
    @patch('tempfile.NamedTemporaryFile')
    def test_view_logs(self, mock_temp, mock_popen):
        # Setup mock file
        mock_file = MagicMock()
        mock_temp.return_value = mock_file
        
        # Call function
        tray_app.view_logs()
        
        # Verify subprocess popen was called to launch terminal window
        self.assertTrue(mock_popen.called)
        
    @patch('subprocess.Popen')
    def test_start_backend(self, mock_popen):
        mock_popen.return_value = MagicMock()
        proc = tray_app.start_backend(sys.executable, "/dummy/backend")
        self.assertIsNotNone(proc)
        self.assertTrue(mock_popen.called)

    @patch('subprocess.Popen')
    def test_start_frontend(self, mock_popen):
        mock_popen.return_value = MagicMock()
        proc = tray_app.start_frontend(sys.executable, "/dummy/root")
        self.assertIsNotNone(proc)
        self.assertTrue(mock_popen.called)
