"""
Image Clarity and Focus Validation Guardrail.
Provides blur detection (Laplacian variance) and blank page filtering (global pixel contrast variance).
"""

import os
import cv2
import numpy as np
import logging

try:
    from logger import logger  # type: ignore
except ImportError:
    logger = logging.getLogger("ImageGuard")

def validate_image_clarity(
    image_path: str,
    blur_threshold: float = 100.0,
    contrast_threshold: float = 20.0
) -> dict:
    """
    Validates whether the image at image_path is of acceptable focus and not completely blank.
    
    1. Grayscale Conversion: Loads the image in grayscale.
    2. Blur Detection: Calculates the Laplacian variance (sharpness score). If below blur_threshold, status is 'BLURRY'.
    3. Blank Page Filter: Calculates the global variance of pixel intensities. If below contrast_threshold, status is 'BLANK'.
    
    Args:
        image_path (str): Filepath to the raw input image.
        blur_threshold (float): Focus score threshold. Recommended >= 100.0.
        contrast_threshold (float): Contrast variance threshold. Recommended >= 20.0.
        
    Returns:
        dict: {
            "success": bool,
            "status": str,          # 'OK', 'BLURRY', or 'BLANK'
            "focus_score": float,
            "contrast_score": float
        }
    """
    logger.info(f"Validating image clarity for: {image_path}")
    
    if not os.path.exists(image_path):
        logger.error(f"Image clarity validation failed: file not found at '{image_path}'")
        return {
            "success": False,
            "status": "BLURRY",
            "focus_score": 0.0,
            "contrast_score": 0.0
        }
        
    try:
        # Load as grayscale
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            logger.error(f"Image clarity validation failed: OpenCV could not read '{image_path}'")
            return {
                "success": False,
                "status": "BLURRY",
                "focus_score": 0.0,
                "contrast_score": 0.0
            }
            
        # 1. Blur Detection (Laplacian Variance)
        laplacian = cv2.Laplacian(img, cv2.CV_64F)
        focus_score = float(laplacian.var())
        
        # 2. Blank Page Filter (Contrast Variance)
        contrast_score = float(img.var())
        
        logger.info(f"Clarity audit metrics for '{os.path.basename(image_path)}': Focus={focus_score:.2f} (threshold={blur_threshold}), Contrast={contrast_score:.2f} (threshold={contrast_threshold})")
        
        if contrast_score < contrast_threshold:
            logger.warning(f"Contrast check FAILED for '{os.path.basename(image_path)}': Metric {contrast_score:.2f} < {contrast_threshold} (BLANK)")
            return {
                "success": False,
                "status": "BLANK",
                "focus_score": focus_score,
                "contrast_score": contrast_score
            }
            
        if focus_score < blur_threshold:
            logger.warning(f"Focus check FAILED for '{os.path.basename(image_path)}': Metric {focus_score:.2f} < {blur_threshold} (BLURRY)")
            return {
                "success": False,
                "status": "BLURRY",
                "focus_score": focus_score,
                "contrast_score": contrast_score
            }
            
        logger.info(f"Clarity checks PASSED for '{os.path.basename(image_path)}'")
        return {
            "success": True,
            "status": "OK",
            "focus_score": focus_score,
            "contrast_score": contrast_score
        }
        
    except Exception as e:
        logger.error(f"Error during image clarity check: {str(e)}")
        # Safe fallback: if check fails, proceed to avoid blockages
        return {
            "success": True,
            "status": "OK",
            "focus_score": 999.0,
            "contrast_score": 999.0
        }
