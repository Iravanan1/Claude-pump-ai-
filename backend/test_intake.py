import os
import sys
import unittest
import numpy as np
import cv2
import fitz
from unittest.mock import MagicMock

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import intake

class TestIntake(unittest.TestCase):
    def test_generate_matrix_hash(self):
        """Verifies stable hash generation for CV2 matrices."""
        img1 = np.zeros((100, 100, 3), dtype=np.uint8)
        img2 = np.zeros((100, 100, 3), dtype=np.uint8)
        img3 = np.ones((100, 100, 3), dtype=np.uint8) * 255
        
        hash1 = intake.generate_matrix_hash(img1)
        hash2 = intake.generate_matrix_hash(img2)
        hash3 = intake.generate_matrix_hash(img3)
        
        self.assertEqual(hash1, hash2)
        self.assertNotEqual(hash1, hash3)
        self.assertEqual(len(hash1), 64)

    def test_convert_upload_to_cv2_matrices_image(self):
        """Tests image decoding from UploadFile."""
        # 1. Create a dummy OpenCV image and encode to bytes
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        cv2.putText(img, "Mock Page", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        success, encoded = cv2.imencode(".png", img)
        self.assertTrue(success)
        image_bytes = encoded.tobytes()
        
        # 2. Mock a FastAPI UploadFile object
        mock_file = MagicMock()
        mock_file.filename = "test_register.png"
        mock_file.file = MagicMock()
        mock_file.file.read.return_value = image_bytes
        
        # 3. Call conversion
        result = intake.convert_upload_to_cv2_matrices(mock_file)
        
        self.assertEqual(len(result), 1)
        page_idx, cv2_matrix, page_filename = result[0]
        self.assertEqual(page_idx, 0)
        self.assertEqual(cv2_matrix.shape, (150, 200, 3))
        self.assertEqual(page_filename, "test_register.png")

    def test_convert_upload_to_cv2_matrices_pdf(self):
        """Tests PDF page rendering from UploadFile using PyMuPDF."""
        # 1. Generate real PDF bytes on the fly
        doc = fitz.open()
        p1 = doc.new_page(width=300, height=400)
        p1.insert_text(fitz.Point(30, 100), "Mock Register Page 1")
        p2 = doc.new_page(width=300, height=400)
        p2.insert_text(fitz.Point(30, 100), "Mock Register Page 2")
        pdf_bytes = doc.write()
        doc.close()
        
        # 2. Mock a FastAPI UploadFile object
        mock_file = MagicMock()
        mock_file.filename = "accounting_pages.pdf"
        mock_file.file = MagicMock()
        mock_file.file.read.return_value = pdf_bytes
        
        # 3. Call conversion
        result = intake.convert_upload_to_cv2_matrices(mock_file)
        
        # We expect 2 rendered pages
        self.assertEqual(len(result), 2)
        
        p1_idx, p1_matrix, p1_filename = result[0]
        self.assertEqual(p1_idx, 0)
        self.assertEqual(p1_filename, "accounting_pages_page_1.png")
        self.assertTrue(p1_matrix.shape[0] > 0)
        
        p2_idx, p2_matrix, p2_filename = result[1]
        self.assertEqual(p2_idx, 1)
        self.assertEqual(p2_filename, "accounting_pages_page_2.png")
        self.assertTrue(p2_matrix.shape[0] > 0)

if __name__ == "__main__":
    unittest.main()
