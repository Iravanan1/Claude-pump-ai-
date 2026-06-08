import cv2
import numpy as np
import logging
import os
import uuid
import time

from cleanup import enforce_stream_disposal, with_stream_disposal

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Processor")

# Define processed images storage path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BACKEND_DIR, "processed_images")
os.makedirs(PROCESSED_DIR, exist_ok=True)

def deskew_image(img: np.ndarray, threshold_angle: float = 5.0) -> tuple[np.ndarray, float]:
    """
    Detects the skew angle of notebook lines or text block and straightens the image 
    if the angle exceeds the threshold_angle (in degrees).
    """
    logger.info("Checking image skew angle...")
    try:
        h, w = img.shape[:2]
        
        # 1. Generate edge map to locate lines
        # First blur slightly to reduce high frequency noise
        blurred = cv2.GaussianBlur(img, (3, 3), 0)
        edges = cv2.Canny(blurred, 50, 150, apertureSize=3)
        
        # 2. Run probabilistic Hough Line Transform
        # We look for long horizontal notebook lines
        min_line_length = w // 4
        lines = cv2.HoughLinesP(
            edges, 
            rho=1, 
            theta=np.pi/180, 
            threshold=80, 
            minLineLength=min_line_length, 
            maxLineGap=15
        )
        
        if lines is None:
            logger.info("No horizontal notebook lines detected for deskewing.")
            return img, 0.0
            
        angles = []
        for line in lines:
            for x1, y1, x2, y2 in line:
                # Compute line slope angle in degrees
                angle = np.arctan2(y2 - y1, x2 - x1) * 180.0 / np.pi
                # Filter out lines that are highly vertical or noisy (keep horizontal lines between -45 and 45 deg)
                if -45.0 < angle < 45.0:
                    angles.append(angle)
                    
        if not angles:
            logger.info("No horizontal-ish notebook line segments discovered.")
            return img, 0.0
            
        median_angle = np.median(angles)
        logger.info(f"Detected page skew angle: {median_angle:.2f} degrees")
        
        # 3. Rotate image if angle exceeds our threshold
        if abs(median_angle) >= threshold_angle:
            logger.info(f"Straightening page by {-median_angle:.2f} degrees (skew exceeds threshold of {threshold_angle}°)")
            center = (w // 2, h // 2)
            # Obtain 2D rotation matrix
            rot_mat = cv2.getRotationMatrix2D(center, median_angle, 1.0)
            # Perform affine transformation padding empty boundaries with white pixels
            rotated = cv2.warpAffine(
                img, 
                rot_mat, 
                (w, h), 
                flags=cv2.INTER_CUBIC, 
                borderMode=cv2.BORDER_CONSTANT, 
                borderValue=255
            )
            return rotated, median_angle
            
        logger.info("Page skew is within tolerance (<= 5 deg). Skipping rotation.")
        return img, 0.0
        
    except Exception as e:
        logger.warning(f"Error during deskewing: {str(e)}. Proceeding with original image.")
        return img, 0.0

@with_stream_disposal()
def optimize_register_image(image_path: str) -> str:
    """
    Performs complete image optimization for handwritten daily registers.
    
    Pipeline:
    1. Grayscale conversion.
    2. Background extraction and shadow removal via dilation and heavy median blur subtraction.
    3. Contrast normalization using CLAHE (adaptive hist eq) and min-max normalization.
    4. Auto-deskewing (straightening if tilted > 5 degrees).
    5. Caches the resulting optimized image in backend/processed_images/ folder.
    
    Args:
        image_path (str): Filepath to the raw input image.
        
    Returns:
        str: Filepath to the optimized preprocessed image.
    """
    logger.info(f"Starting advanced optimization pipeline for {image_path}...")
    try:
        # Load image from disk
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Input image path '{image_path}' does not exist!")
            
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Failed to load image at '{image_path}' using OpenCV.")
            
        # 1. Grayscale Conversion
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 2. Morphological Background & Shadow Removal
        # Isolate background shadows using dilation
        dilated = cv2.dilate(gray, np.ones((7, 7), np.uint8))
        # Smooth using a heavy median blur kernel
        bg = cv2.medianBlur(dilated, 21)
        # Subtract background from grayscale image to eliminate illumination gradients
        diff = cv2.absdiff(gray, bg)
        # Invert to recover dark text on clean white page
        shadow_free = 255 - diff
        
        # 3. Contrast Normalization (CLAHE + Min-Max)
        # Apply CLAHE to sharpen faint handwritten pen ink marks
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        contrast_enhanced = clahe.apply(shadow_free)
        # Min-max normalization to stretch visual dynamic range
        normalized = cv2.normalize(contrast_enhanced, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        
        # 4. Auto-Deskewing (Straightening tilted pages)
        optimized_gray, skew_angle = deskew_image(normalized, threshold_angle=5.0)
        
        # 5. Save Output to Caching Folder
        filename = f"opt_{int(time.time())}_{uuid.uuid4().hex[:6]}.png"
        output_path = os.path.join(PROCESSED_DIR, filename)
        
        success = cv2.imwrite(output_path, optimized_gray)
        if not success:
            raise RuntimeError(f"OpenCV failed to write optimized image file to '{output_path}'")
            
        logger.info(f"Advanced image optimization completed! Output saved to: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"Failed to optimize register image safely: {str(e)}")
        # Graceful fallback: return the original image path to ensure the API server never crashes
        logger.warning("Returning original image path as safe fallback.")
        return image_path

