#!/usr/bin/env python3
"""
PetroByte Import Format Compliance Validator.
Audits generated PetroByte CSV files for header alignment, balanced entries,
string normalizations, and outputs detailed row-level diagnostics on error.
"""

import os
import pandas as pd
import logging

logger = logging.getLogger("PetroByteValidator")

def validate_petrobyte_csv_format(generated_csv_path: str) -> bool:
    """
    Validates the generated PetroByte CSV import file for strict compliance.
    Cleans up minor string irregularities and updates the file in place.
    Raises ValueError on layout, data type, or mathematical imbalances.
    """
    logger.info(f"Auditing PetroByte CSV file: {os.path.abspath(generated_csv_path)}")
    
    if not os.path.exists(generated_csv_path):
        err_msg = f"CSV file not found at path: {generated_csv_path}"
        logger.error(err_msg)
        raise FileNotFoundError(err_msg)
        
    try:
        # 1. Load CSV file
        df = pd.read_csv(generated_csv_path)
    except Exception as read_err:
        err_msg = f"Failed to read CSV file: {str(read_err)}"
        logger.error(err_msg)
        raise ValueError(err_msg)
        
    # 2. Header Alignment Check
    expected_headers = ["Date", "Ledger Name", "Voucher Type", "Account Debit", "Account Credit", "Narration"]
    actual_headers = list(df.columns)
    
    if actual_headers != expected_headers:
        err_msg = (
            "Header alignment mismatch! "
            f"Expected: {expected_headers}, "
            f"Found: {actual_headers}"
        )
        logger.error("PETROBYTE VALIDATION FAILURE:")
        logger.error(err_msg)
        logger.error("Row Index: 0 (Headers)")
        raise ValueError(err_msg)
        
    # 3. Voucher Type and Row Checks
    valid_voucher_types = {"Sale", "Receipt", "Payment"}
    
    for idx, row in df.iterrows():
        v_type = str(row["Voucher Type"]).strip()
        if v_type not in valid_voucher_types:
            row_idx = idx + 2 # 1-indexed plus header row offset
            err_msg = (
                f"Invalid Voucher Type '{v_type}' at row index {row_idx}. "
                f"Must be one of: {list(valid_voucher_types)}"
            )
            logger.error("PETROBYTE VALIDATION FAILURE:")
            logger.error(err_msg)
            logger.error(f"Row details - Date: {row['Date']}, Ledger: {row['Ledger Name']}")
            raise ValueError(err_msg)
            
    # 4. String Normalization Guard (Narration cleansing)
    def clean_narration(val):
        if pd.isna(val) or val is None:
            return ""
        s = str(val)
        # Remove carriage returns and newlines
        s = s.replace("\r", " ").replace("\n", " ")
        # Replace multiple spaces with a single space
        while "  " in s:
            s = s.replace("  ", " ")
        # Strip trailing commas
        s = s.strip()
        while s.endswith(","):
            s = s[:-1].strip()
        return s
        
    df["Narration"] = df["Narration"].apply(clean_narration)
    
    # Write back normalized file in place
    try:
        df.to_csv(generated_csv_path, index=False, encoding="utf-8-sig")
        logger.info("String normalization cleanups committed back to CSV file.")
    except Exception as write_err:
        err_msg = f"Failed to rewrite cleaned CSV: {str(write_err)}"
        logger.error(err_msg)
        raise ValueError(err_msg)
        
    # 5. Balanced Entry Verification (per day block)
    # Group by Date and verify sums
    grouped = df.groupby("Date")
    
    for date_val, group in grouped:
        debit_sum = round(float(group["Account Debit"].sum()), 2)
        credit_sum = round(float(group["Account Credit"].sum()), 2)
        
        if abs(debit_sum - credit_sum) > 0.009: # Allow less than a paisa rounding margin
            # Find the indices of rows matching this date to display in diagnostics
            matching_indices = group.index.tolist()
            # Convert to actual CSV 1-based row numbers (adding 2 for header + index offset)
            csv_row_numbers = [idx + 2 for idx in matching_indices]
            
            err_msg = (
                f"Mathematical imbalance detected for date block '{date_val}'! "
                f"Total Debit: ₹{debit_sum:.2f}, Total Credit: ₹{credit_sum:.2f}. "
                f"Variance: ₹{abs(debit_sum - credit_sum):.2f}"
            )
            logger.error("PETROBYTE VALIDATION FAILURE:")
            logger.error(err_msg)
            logger.error(f"Imbalanced rows range (CSV line numbers): {csv_row_numbers}")
            logger.error(f"Ledgers involved: {group['Ledger Name'].tolist()}")
            raise ValueError(err_msg)
            
    logger.info("✓ PetroByte CSV import format compliance validation passed successfully!")
    return True
