import os
import fitz
import numpy as np
import cv2
import hashlib
from logger import logger
from cleanup import close_pdf_doc, flush_memory, enforce_stream_disposal

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BACKEND_DIR, "processed_images")
os.makedirs(PROCESSED_DIR, exist_ok=True)

def generate_matrix_hash(cv2_matrix) -> str:
    """
    Computes a stable SHA-256 hash of an OpenCV matrix by encoding it to PNG bytes.
    """
    success, encoded_img = cv2.imencode('.png', cv2_matrix)
    if not success:
        raise ValueError("Failed to encode CV2 matrix to PNG.")
    return hashlib.sha256(encoded_img.tobytes()).hexdigest()

def convert_upload_to_cv2_matrices(upload_file) -> list:
    """
    Intakes an UploadFile (image or PDF).
    Returns a list of tuples: (page_index, cv2_matrix, page_filename)
    """
    filename = upload_file.filename
    ext = os.path.splitext(filename)[1].lower()
    
    # Standard reset to support safe re-reads
    upload_file.file.seek(0)
    file_bytes = upload_file.file.read()
    
    cv2_matrices = []
    
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
        logger.info(f"Intaking raw image: {filename}...")
        nparr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None:
            cv2_matrices.append((0, img, filename))
        else:
            logger.warning(f"Failed to decode image file: {filename}. Using placeholder fallback matrix.")
            placeholder = np.zeros((100, 100, 3), dtype=np.uint8)
            cv2_matrices.append((0, placeholder, filename))
            
    elif ext == ".pdf":
        logger.info(f"Intaking PDF document: {filename}...")
        doc = None
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page_idx, page in enumerate(doc):
                # Matrix(2, 2) scale represents high-resolution render (approx. 150-200 DPI)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                cv2_img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                page_filename = f"{os.path.splitext(filename)[0]}_page_{page_idx + 1}.png"
                cv2_matrices.append((page_idx, cv2_img, page_filename))
                # Release the intermediate raw numpy array immediately
                del img
            logger.info(f"Successfully rendered {len(cv2_matrices)} pages from PDF: {filename}.")
        except Exception as e:
            logger.error(f"Failed to render PDF pages for {filename}: {str(e)}")
            raise e
        finally:
            # Guarantee the fitz document handle is released even on error
            close_pdf_doc(doc)
            
    else:
        logger.warning(f"Unsupported file format: {ext} for file: {filename}")
        
    # Flush memory after every file extraction call (images or PDF)
    flush_memory()
    return cv2_matrices

def save_matrix_to_processed(cv2_matrix, filename: str) -> str:
    """
    Saves a clean OpenCV matrix into the /processed_images/ directory.
    Returns the absolute path of the saved file.
    """
    target_path = os.path.join(PROCESSED_DIR, filename)
    cv2.imwrite(target_path, cv2_matrix)
    logger.info(f"Saved processed sheet frame to: {target_path}")
    return target_path
