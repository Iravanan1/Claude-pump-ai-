import os
import sys
import sqlite3
import shutil
import unittest
import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import main
import init_db
import exporter
import crypto_vault

class TestMainAPI(unittest.TestCase):
    def setUp(self):
        # Override database and Excel path in both main, init_db, and exporter
        self.original_db = main.DB_PATH
        self.original_excel = main.EXCEL_PATH
        self.original_init_db = init_db.DB_PATH
        self.original_exporter_db = exporter.DB_PATH
        self.original_exporter_excel = exporter.DEFAULT_EXCEL_PATH
        
        self.test_db = os.path.join(BACKEND_DIR, "test_ledger.db")
        self.test_excel = os.path.join(BACKEND_DIR, "test_ledger.xlsx")
        
        main.DB_PATH = self.test_db
        main.EXCEL_PATH = self.test_excel
        init_db.DB_PATH = self.test_db
        exporter.DB_PATH = self.test_db
        exporter.DEFAULT_EXCEL_PATH = self.test_excel
        
        # Clean up files if they already exist
        for path in [self.test_db, self.test_db + "-wal", self.test_db + "-shm", self.test_excel]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            
        # Re-initialize test tables
        init_db.initialize_database()
        main.init_db()
        
        # Setup FastAPI TestClient
        self.client = TestClient(main.app)

    def tearDown(self):
        # Restore original paths
        main.DB_PATH = self.original_db
        main.EXCEL_PATH = self.original_excel
        init_db.DB_PATH = self.original_init_db
        exporter.DB_PATH = self.original_exporter_db
        exporter.DEFAULT_EXCEL_PATH = self.original_exporter_excel
        
        # Clean up files
        for path in [self.test_db, self.test_db + "-wal", self.test_db + "-shm", self.test_excel]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            
        # Clean up uploaded raw photos folder
        upload_dir = os.path.join(BACKEND_DIR, "uploaded_raw_photos")
        if os.path.exists(upload_dir):
            shutil.rmtree(upload_dir)

    @patch("image_guard.validate_image_clarity")
    @patch("processor.optimize_register_image")
    @patch("ai_engine.analyze_register_sheet")
    def test_upload_endpoint(self, mock_analyze, mock_optimize, mock_clarity):
        """Verifies that POST /api/upload successfully saves file, preprocesses, and calls AI sheet analysis."""
        mock_clarity.return_value = {"success": True, "status": "OK", "focus_score": 999.0, "contrast_score": 999.0}
        mock_optimize.return_value = "/mock/path/optimized.png"
        mock_analyze.return_value = {
            "date": "2026-06-05",
            "validation_status": "balanced",
            "mathematical_warnings": [],
            "total_cash_calculated": 15000.0,
            "total_credit_sales": 2000.0
        }
        
        # Prepare a mock image file
        file_content = b"fake_png_data_123"
        files = {"image": ("test_register.png", file_content, "image/png")}
        
        response = self.client.post("/api/upload", files=files)
        self.assertEqual(response.status_code, 200)
        
        json_data = response.json()
        self.assertEqual(json_data["date"], "2026-06-05")
        self.assertEqual(json_data["validation_status"], "balanced")
        
        # Verify optimize and analyze mocks were triggered
        mock_optimize.assert_called_once()
        mock_analyze.assert_called_with("/mock/path/optimized.png")

    @patch("image_guard.validate_image_clarity")
    @patch("processor.optimize_register_image")
    @patch("ai_engine.analyze_register_sheet")
    def test_upload_endpoint_pdf(self, mock_analyze, mock_optimize, mock_clarity):
        """Verifies that POST /api/upload successfully parses PDF documents, rendering pages and calling vision parsing."""
        mock_clarity.return_value = {"success": True, "status": "OK", "focus_score": 999.0, "contrast_score": 999.0}
        mock_optimize.return_value = "/mock/path/optimized.png"
        mock_analyze.return_value = {
            "date": "2026-06-05",
            "validation_status": "balanced",
            "mathematical_warnings": []
        }
        
        # 1. Generate real PDF bytes on the fly
        import fitz
        doc = fitz.open()
        p1 = doc.new_page(width=300, height=400)
        p1.insert_text(fitz.Point(30, 100), "Mock Register Page 1")
        pdf_bytes = doc.write()
        doc.close()
        
        # 2. Upload file via list 'files' parameter
        files = [("files", ("accounting_pages.pdf", pdf_bytes, "application/pdf"))]
        
        response = self.client.post("/api/upload", files=files)
        self.assertEqual(response.status_code, 200)
        
        json_data = response.json()
        self.assertEqual(json_data["page_index"], 0)
        self.assertEqual(json_data["original_filename"], "accounting_pages.pdf")
        
        mock_optimize.assert_called_once()

    @patch("exporter.export_db_to_excel")
    def test_save_ledger_day_endpoint(self, mock_export):
        """Verifies that POST /api/save-ledger-day correctly updates DB summary, ledger list, and runs Excel exporter."""
        mock_export.return_value = self.test_excel
        
        payload = {
            "date": "2026-06-05",
            "total_calculated_liters_hsd": 150.0,
            "total_calculated_liters_ms": 250.0,
            "total_cash_calculated": 42000.0,
            "total_credit_sales": 5000.0,
            "total_testing_deductions": 5.0,
            "validation_status": "balanced",
            "mathematical_warnings": [],
            "nozzles": [
                {
                    "nozzle_name": "Nozzle 1",
                    "fuel_type": "HSD",
                    "opening": 1000,
                    "closing": 1150,
                    "sales_liters_calculated": 150,
                    "rate": 90,
                    "amount_calculated": 13500
                }
            ],
            "credit_sales": [
                {
                    "party_name": "Sharma Transport",
                    "vehicle_no": "HR-55-XY-7890",
                    "amount": 5000,
                    "remarks": "Udhaar credit"
                }
            ],
            "cash_expenses": [
                {
                    "party_name": "Office Stationary",
                    "amount": 450,
                    "remarks": "Pens and paper"
                }
            ],
            "card_settlements": [
                {
                    "machine_provider": "HDFC POS",
                    "gross_swipes_copied": 12400.0
                },
                {
                    "machine_provider": "SBI Touch",
                    "gross_swipes_copied": 8500.0
                }
            ]
        }
        
        response = self.client.post("/api/save-ledger-day", json=payload)
        self.assertEqual(response.status_code, 200)
        
        json_data = response.json()
        self.assertEqual(json_data["status"], "success")
        
        # Verify records in the test database
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Check daily_summary table
        cursor.execute("SELECT * FROM daily_summary WHERE date = '2026-06-05'")
        summary = cursor.fetchone()
        self.assertIsNotNone(summary)
        self.assertEqual(summary[1], 150.0) # total_hsd_liters
        self.assertEqual(summary[2], 250.0) # total_ms_liters
        self.assertEqual(summary[3], 42000.0) # total_cash_calculated
        self.assertEqual(summary[4], 5000.0) # total_credit_sales
        self.assertEqual(summary[5], 5.0) # total_testing_deductions
        self.assertEqual(summary[6], 1) # is_verified (1 = True because validation_status is balanced and no warnings)
        
        # Check ledger_entries table
        cursor.execute("SELECT * FROM ledger_entries WHERE date = '2026-06-05' AND type = 'udhaar'")
        udhaar = cursor.fetchone()
        self.assertIsNotNone(udhaar)
        self.assertEqual(crypto_vault.decrypt_field(udhaar[2]), "Sharma Transport")
        self.assertEqual(udhaar[3], "HR-55-XY-7890")
        self.assertEqual(crypto_vault.decrypt_field(udhaar[4], return_type=float), 5000.0)
        
        cursor.execute("SELECT * FROM ledger_entries WHERE date = '2026-06-05' AND type = 'expense'")
        expense = cursor.fetchone()
        self.assertIsNotNone(expense)
        self.assertEqual(crypto_vault.decrypt_field(expense[2]), "Office Stationary")
        self.assertEqual(crypto_vault.decrypt_field(expense[4], return_type=float), 450.0)
        
        # Check card_settlements table
        cursor.execute("SELECT * FROM card_settlements WHERE date = '2026-06-05' ORDER BY id ASC")
        settlements = cursor.fetchall()
        self.assertEqual(len(settlements), 2)
        
        # HDFC POS (0.9% MDR expected)
        self.assertEqual(settlements[0][2], "HDFC POS")
        self.assertEqual(settlements[0][3], 12400.0)
        self.assertEqual(settlements[0][4], 111.60) # 12400 * 0.009
        self.assertEqual(settlements[0][5], 12288.40)
        self.assertEqual(settlements[0][6], "Pending")
        
        # SBI Touch (0.75% MDR expected)
        self.assertEqual(settlements[1][2], "SBI Touch")
        self.assertEqual(settlements[1][3], 8500.0)
        self.assertEqual(settlements[1][4], 63.75) # 8500 * 0.0075
        self.assertEqual(settlements[1][5], 8436.25)
        self.assertEqual(settlements[1][6], "Pending")
        
        # Check daily_ledger table
        cursor.execute("SELECT * FROM daily_ledger WHERE date = '2026-06-05'")
        ledger = cursor.fetchone()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger[2], 400.0) # total_sales_liters (150 + 250)
        self.assertEqual(ledger[3], 42000.0) # total_amount_inr
        self.assertEqual(ledger[8], 5000.0) # udhaar_sales
        self.assertEqual(ledger[9], 450.0) # expenses_amount
        self.assertEqual(ledger[10], "valid") # validation_status = valid
        
        conn.close()
        
        # Verify master spreadsheet auto-exporter was invoked
        mock_export.assert_called_once()

    def test_review_queue_endpoint(self):
        """Verifies that GET /api/review-queue retrieves all entries with needs_review or math_discrepancy validation status."""
        # Insert a needs_review entry into daily_ledger database
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO daily_ledger 
            (date, total_sales_liters, total_amount_inr, cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales, expenses_amount, validation_status, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "2026-06-10", 400.0, 42000.0, 36550.0, 0.0, 0.0, 0.0, 5000.0, 450.0, "needs_review", "{}"
        ))
        conn.commit()
        conn.close()
        
        response = self.client.get("/api/review-queue")
        self.assertEqual(response.status_code, 200)
        
        queue = response.json()
        self.assertTrue(len(queue) >= 1)
        self.assertEqual(queue[0]["date"], "2026-06-10")
        self.assertEqual(queue[0]["validation_status"], "needs_review")

    def test_reconciliation_endpoints(self):
        """Verifies GET /api/reconciliation and POST /api/reconciliation operational calculations."""
        # 1. Setup mock data in daily_summary and daily_ledger
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales)
            VALUES ('2026-06-15', 500.0, 300.0, 80000.0, 15000.0)
        """)
        cursor.execute("""
            INSERT INTO daily_ledger (date, total_sales_liters, total_amount_inr, cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales)
            VALUES ('2026-06-15', 800.0, 80000.0, 50000.0, 15000.0, 0.0, 0.0, 15000.0)
        """)
        conn.commit()
        conn.close()

        # 2. Test GET reconciliation endpoint for new date (defaults expected)
        response = self.client.get("/api/reconciliation?date=2026-06-15")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["date"], "2026-06-15")
        self.assertEqual(data["expected_hsd_book_stock"], -500.0) # 0 + 0 - 500 = -500
        
        # 3. Test POST reconciliation endpoint to save and get calculations
        payload = {
            "date": "2026-06-15",
            "hsd_opening_dip_liters": 5000.0,
            "hsd_receipt_liters": 2000.0,
            "hsd_closing_dip_liters": 6480.0, # Expected Book: 5000 + 2000 - 500 = 6500. Variance: -20.0
            "ms_opening_dip_liters": 3000.0,
            "ms_receipt_liters": 0.0,
            "ms_closing_dip_liters": 2700.0, # Expected Book: 3000 - 300 = 2700. Variance: 0.0
            "actual_cash_deposited": 49500.0,
            "digital_wallet_settlements": 15000.0,
            "logged_udhaar_entries": 15000.0 # Reconciled Total = 79500. Calculated: 80000. Variance: -500.0
        }
        
        # Patch the active reconciliation module DB path to target test DB
        import reconciliation
        original_recon_db = reconciliation.DB_PATH
        reconciliation.DB_PATH = self.test_db
        try:
            response = self.client.post("/api/reconciliation", json=payload)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["expected_hsd_book_stock"], 6500.0)
            self.assertEqual(data["hsd_variance_liters"], -20.0)
            self.assertEqual(data["expected_ms_book_stock"], 2700.0)
            self.assertEqual(data["ms_variance_liters"], 0.0)
            self.assertEqual(data["cash_short_or_over"], -500.0)
            self.assertEqual(data["cash_status"], "shortage")
        finally:
            reconciliation.DB_PATH = original_recon_db
            
    def test_save_ledger_day_credit_limit_warning(self):
        # Setup mock limit in DB for Gopalram Ji Dhaba
        import credit_guard
        credit_guard.init_credit_db(self.test_db)
        credit_guard.set_credit_limit("Gopalram Ji Dhaba", 10000.0, 80.0, db_path=self.test_db)
        
        # Post a credit sale that exceeds the limit
        payload = {
            "date": "2026-06-10",
            "total_calculated_liters_hsd": 100.0,
            "total_calculated_liters_ms": 100.0,
            "total_cash_calculated": 20000.0,
            "total_credit_sales": 12000.0,
            "total_testing_deductions": 0.0,
            "validation_status": "balanced",
            "mathematical_warnings": [],
            "nozzles": [],
            "credit_sales": [
                {
                    "party_name": "Gopalram Ji Dhaba",
                    "vehicle_no": "N/A",
                    "amount": 12000.0, # Exceeds limit 10000.0
                    "remarks": "Udhaar credit"
                }
            ],
            "cash_expenses": []
        }
        
        # Patch main DB_PATH to target our test DB
        main.DB_PATH = self.test_db
        try:
            response = self.client.post("/api/save-ledger-day", json=payload)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            self.assertIn("credit_alert", data)
            self.assertEqual(data["credit_alert"], "Warning: Gopalram Ji Dhaba balance has exceeded their credit cap")
        finally:
            main.DB_PATH = self.original_db
            
    def test_save_ledger_day_with_dsm_shifts(self):
        # 1. Initialize DSM DB on test db
        import dsm_tracker
        dsm_tracker.init_dsm_db(self.test_db)
        
        # 2. Setup POST payload containing DSM shift logs
        payload = {
            "date": "2026-06-15",
            "total_calculated_liters_hsd": 100.0,
            "total_calculated_liters_ms": 100.0,
            "total_cash_calculated": 20000.0,
            "total_credit_sales": 0.0,
            "total_testing_deductions": 0.0,
            "validation_status": "balanced",
            "mathematical_warnings": [],
            "nozzles": [],
            "credit_sales": [],
            "cash_expenses": [],
            "dsm_shifts": [
                {
                    "dsm_name": "Ramesh",
                    "shift_type": "Day",
                    "assigned_nozzles": ["MS-1", "MS-2"],
                    "cash_handed_over": 45000.0,
                    "digital_slips_value": 1500.0,
                    "calculated_shortage_or_excess": -120.0
                }
            ]
        }
        
        # Patch main DB_PATH to target our test DB
        main.DB_PATH = self.test_db
        try:
            response = self.client.post("/api/save-ledger-day", json=payload)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            
            # 3. Assert database entries
            conn = sqlite3.connect(self.test_db)
            cursor = conn.cursor()
            
            # Verify dsm_shifts table entries
            cursor.execute("""
                SELECT dsm_name, shift_type, assigned_nozzles, cash_handed_over, digital_slips_value, calculated_shortage_or_excess
                FROM dsm_shifts WHERE date = '2026-06-15'
            """)
            dsm_row = cursor.fetchone()
            self.assertIsNotNone(dsm_row)
            self.assertEqual(dsm_row[0], "Ramesh")
            self.assertEqual(dsm_row[1], "Day")
            self.assertEqual(dsm_row[2], "MS-1, MS-2")
            self.assertEqual(dsm_row[3], 45000.0)
            self.assertEqual(dsm_row[4], 1500.0)
            self.assertEqual(dsm_row[5], -120.0)
            
            # Verify encrypted raw_data in daily_ledger table contains dsm_shifts
            cursor.execute("SELECT raw_data FROM daily_ledger WHERE date = '2026-06-15'")
            ledger_row = cursor.fetchone()
            self.assertIsNotNone(ledger_row)
            conn.close()
            
            import json
            import crypto_vault
            raw_json_str = ledger_row[0]
            decrypted = crypto_vault.decrypt_raw_data(json.loads(raw_json_str))
            
            self.assertIn("dsm_shifts", decrypted)
            self.assertEqual(len(decrypted["dsm_shifts"]), 1)
            self.assertEqual(decrypted["dsm_shifts"][0]["dsm_name"], "Ramesh")
            self.assertEqual(decrypted["dsm_shifts"][0]["calculated_shortage_or_excess"], -120.0)
            
        finally:
            main.DB_PATH = self.original_db
            
    def test_save_ledger_day_with_lube_sales(self):
        # 1. Initialize Lubricant database
        import lube_sales
        lube_sales.init_lube_db(self.test_db)
        
        # 2. Setup POST payload containing lube sales logs
        # expected base cash calculated = 20000.0 (fuel sales)
        payload = {
            "date": "2026-06-20",
            "total_calculated_liters_hsd": 100.0,
            "total_calculated_liters_ms": 100.0,
            "total_cash_calculated": 20000.0,
            "total_credit_sales": 0.0,
            "total_testing_deductions": 0.0,
            "validation_status": "balanced",
            "mathematical_warnings": [],
            "nozzles": [],
            "credit_sales": [],
            "cash_expenses": [],
            "lube_sales": [
                {
                    "item_name": "Servo 4T 1L",
                    "quantity_sold": 2.0,
                    "unit_price": 350.0,
                    "total_item_revenue": 700.0
                }
            ]
        }
        
        # Patch main DB_PATH to target our test DB
        main.DB_PATH = self.test_db
        try:
            response = self.client.post("/api/save-ledger-day", json=payload)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            
            # 3. Assert database entries
            conn = sqlite3.connect(self.test_db)
            cursor = conn.cursor()
            
            # Verify inventory_sales table entries
            cursor.execute("""
                SELECT item_name, quantity_sold, unit_price, total_item_revenue
                FROM inventory_sales WHERE date = '2026-06-20'
            """)
            lube_row = cursor.fetchone()
            self.assertIsNotNone(lube_row)
            self.assertEqual(lube_row[0], "Servo 4T 1L")
            self.assertEqual(lube_row[1], 2.0)
            self.assertEqual(lube_row[2], 350.0)
            self.assertEqual(lube_row[3], 700.0)
            
            # Verify daily_summary cash totals have been updated to combined fuel + lube cash (expected total cash = 20000 + 700 = 20700)
            cursor.execute("SELECT total_cash_calculated FROM daily_summary WHERE date = '2026-06-20'")
            summary_row = cursor.fetchone()
            self.assertIsNotNone(summary_row)
            # Since no nozzles were sent, fallback ledger defaults are used
            # expected total expected calculations: cash_tender + udhaar_sales + expenses_amount + lube = 20000 + 700 = 20700.0
            self.assertEqual(summary_row[0], 20700.0)
            
            # Verify encrypted raw_data in daily_ledger table contains lube_sales
            cursor.execute("SELECT raw_data FROM daily_ledger WHERE date = '2026-06-20'")
            ledger_row = cursor.fetchone()
            self.assertIsNotNone(ledger_row)
            conn.close()
            
            import json
            import crypto_vault
            raw_json_str = ledger_row[0]
            decrypted = crypto_vault.decrypt_raw_data(json.loads(raw_json_str))
            
            self.assertIn("lube_sales", decrypted)
            self.assertEqual(len(decrypted["lube_sales"]), 1)
            self.assertEqual(decrypted["lube_sales"][0]["item_name"], "Servo 4T 1L")
            self.assertEqual(decrypted["lube_sales"][0]["total_item_revenue"], 700.0)
            
        finally:
            main.DB_PATH = self.original_db

    def test_daily_summary_text_endpoint(self):
        # 1. Populate some mock records in SQLite
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales)
            VALUES ('2026-06-25', 120.5, 230.0, 45000.0, 5000.0)
        """)
        cursor.execute("""
            INSERT OR REPLACE INTO daily_ledger (date, total_sales_liters, total_amount_inr, cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales, expenses_amount, validation_status, raw_data)
            VALUES ('2026-06-25', 350.5, 45000.0, 40000.0, 2000.0, 1000.0, 2000.0, 5000.0, 0.0, 'valid', '{}')
        """)
        cursor.execute("""
            INSERT OR REPLACE INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES ('2026-06-25', ?, 'HR-38-9999', ?, 'udhaar', 'HSD credit sale')
        """, (crypto_vault.encrypt_field("Balaji Transport"), crypto_vault.encrypt_field(5000.0)))
        conn.commit()
        conn.close()

        # Patch main DB_PATH to target our test DB
        main.DB_PATH = self.test_db
        try:
            response = self.client.get("/api/daily-summary-text?date=2026-06-25")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["date"], "2026-06-25")
            
            digest = data["digest"]
            self.assertIn("Daily Pump Summary: 2026-06-25", digest)
            self.assertIn("HSD Sold: 120.5 Liters", digest)
            self.assertIn("MS Sold: 230 Liters", digest)
            self.assertIn("Total Cash Collected: ₹40000", digest)
            self.assertIn("Digital Drops (Paytm/Cards): ₹5000", digest)
            self.assertIn("Total Credit Sales (Udhaar): ₹5000", digest)
            self.assertIn("Major Credit Parties: [Balaji Transport: ₹5000]", digest)
            self.assertIn("Shortages/Variance: ₹5000", digest)
        finally:
            main.DB_PATH = self.original_db

    def test_interlock_check_endpoint(self):
        # 1. Populate some mock records in SQLite
        prec_raw = {
            "image_url": "http://localhost:8000/uploaded_raw_photos/skewed_test_raw.png",
            "nozzles": [
                {"nozzle_name": "MS-1", "opening": 50.0, "closing": 100.0}
            ]
        }
        encrypted_prec = crypto_vault.encrypt_raw_data(prec_raw)
        
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO daily_ledger (date, raw_data)
            VALUES ('2026-06-25', ?)
        """, (json.dumps(encrypted_prec),))
        conn.commit()
        conn.close()

        # Patch main DB_PATH to target our test DB
        main.DB_PATH = self.test_db
        try:
            # Check date '2026-06-26' which is immediately chronologically after '2026-06-25'
            response = self.client.get("/api/interlock-check?date=2026-06-26")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            # Since we did not provide nozzles, and 2026-06-26 is not in DB, it returns empty list of current nozzles, so status is "ok" (no discrepancies found)
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["preceding_date"], "2026-06-25")
            self.assertEqual(data["preceding_image_url"], "http://localhost:8000/uploaded_raw_photos/skewed_test_raw.png")
            
        finally:
            main.DB_PATH = self.original_db

    @patch("repair_kit.rebuild_master_spreadsheet")
    def test_rebuild_master_excel_endpoint(self, mock_rebuild):
        mock_rebuild.return_value = "/mock/path/Pump_Accounts.xlsx"
        
        response = self.client.post("/api/rebuild-master-excel")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["file_path"], "/mock/path/Pump_Accounts.xlsx")
        self.assertIn("reconstructed successfully", data["message"])

    @patch("exporter.export_db_to_excel")
    def test_save_ledger_day_fifo_settlement(self, mock_export):
        """Verifies that saving a ledger day with credit realization settles outstanding udhaar via FIFO."""
        mock_export.return_value = self.test_excel
        
        # 1. Seed outstanding udhaar credit entry
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Seed Gopalram Transport with 10000.0 unpaid credit
        from crypto_vault import encrypt_field, decrypt_field
        party_enc = encrypt_field("Gopalram Transport")
        amt_enc = encrypt_field(10000.0)
        
        # Ensure FIFO columns exist
        from fifo_settler import ensure_fifo_columns
        ensure_fifo_columns(self.test_db)
        
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status, amount_remaining)
            VALUES ('2026-06-01', ?, 'RJ14-1234', ?, 'udhaar', 'Initial Credit', 'UNPAID', NULL)
        """, (party_enc, amt_enc))
        conn.commit()
        conn.close()
        
        # 2. Save a ledger day with a realization of 6000.0
        payload = {
            "date": "2026-06-02",
            "total_calculated_liters_hsd": 0.0,
            "total_calculated_liters_ms": 0.0,
            "total_cash_calculated": 6000.0,
            "total_credit_sales": 0.0,
            "total_testing_deductions": 0.0,
            "validation_status": "balanced",
            "mathematical_warnings": [],
            "nozzles": [],
            "credit_sales": [],
            "cash_expenses": [],
            "credit_realizations": [
                {
                    "party_name": "Gopalram Transport",
                    "amount_received": 6000.0,
                    "payment_mode": "UPI",
                    "bank_utr_or_remarks": "UPI-12345",
                    "linked_invoice_no": "INV-001"
                }
            ]
        }
        
        response = self.client.post("/api/save-ledger-day", json=payload)
        self.assertEqual(response.status_code, 200)
        
        # 3. Check DB state
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Verify the original udhaar entry flipped to PARTIALLY_PAID
        cursor.execute("SELECT payment_status, amount_remaining FROM ledger_entries WHERE type='udhaar'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "PARTIALLY_PAID")
        self.assertEqual(row[1], 4000.0)
        
        # Verify credit_realization table entry
        cursor.execute("SELECT realization_id, payment_mode, bank_utr_or_remarks FROM credit_realizations")
        cr_row = cursor.fetchone()
        self.assertIsNotNone(cr_row)
        self.assertEqual(cr_row[1], "UPI")
        self.assertEqual(cr_row[2], "UPI-12345")
        
        # 4. Settle the remaining 4000.0 with another realization of 5000.0 (overpayment)
        payload2 = {
            "date": "2026-06-03",
            "total_calculated_liters_hsd": 0.0,
            "total_calculated_liters_ms": 0.0,
            "total_cash_calculated": 5000.0,
            "total_credit_sales": 0.0,
            "total_testing_deductions": 0.0,
            "validation_status": "balanced",
            "mathematical_warnings": [],
            "nozzles": [],
            "credit_sales": [],
            "cash_expenses": [],
            "credit_realizations": [
                {
                    "party_name": "Gopalram Transport",
                    "amount_received": 5000.0,
                    "payment_mode": "CASH",
                    "bank_utr_or_remarks": "Handed over cash",
                    "linked_invoice_no": "INV-002"
                }
            ]
        }
        
        response2 = self.client.post("/api/save-ledger-day", json=payload2)
        self.assertEqual(response2.status_code, 200)
        
        # Check database again
        cursor.execute("SELECT payment_status, amount_remaining FROM ledger_entries WHERE type='udhaar'")
        row2 = cursor.fetchone()
        self.assertEqual(row2[0], "FULLY_PAID")
        self.assertEqual(row2[1], 0.0)
        
        # Verify the surplus of 1000.0 was recorded as advance
        cursor.execute("SELECT amount, type, payment_status FROM ledger_entries WHERE type='advance'")
        adv_row = cursor.fetchone()
        self.assertIsNotNone(adv_row)
        decrypted_adv = decrypt_field(adv_row[0], return_type=float)
        self.assertEqual(decrypted_adv, 1000.0)
        self.assertEqual(adv_row[2], "N/A")
        
        conn.close()

    @patch("bank_matcher.parse_bank_statement_pdf")
    def test_reconcile_bank_statement_endpoint(self, mock_parse):
        # 1. Setup mock transactions returned by parse_bank_statement_pdf
        mock_parse.return_value = [
            {
                "bank_name": "SBI",
                "transaction_date": "2026-06-01",
                "description": "UPI/123456/PAYTM",
                "utr_string": "UTR123456",
                "credit_amount": 4500.0,
                "debit_amount": 0.0,
            },
            {
                "bank_name": "SBI",
                "transaction_date": "2026-06-02",
                "description": "UPI/789012/GOPAL",
                "utr_string": "UTR789012",
                "credit_amount": 1200.0,
                "debit_amount": 0.0,
            }
        ]

        # 2. Seed some digital entries in daily_ledger so one matches and one doesn't
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Insert a matching Paytm drop of 4500 on 2026-06-01
        cursor.execute("""
        INSERT OR REPLACE INTO daily_ledger (date, paytm_transfers, upi_tender, card_tender)
        VALUES (?, ?, ?, ?)
        """, ("2026-06-01", 4500.0, 0.0, 0.0))
        
        # Insert a missing UPI drop of 3000 on 2026-06-02 (this should be marked as missing since the statement has 1200, not 3000)
        cursor.execute("""
        INSERT OR REPLACE INTO daily_ledger (date, paytm_transfers, upi_tender, card_tender)
        VALUES (?, ?, ?, ?)
        """, ("2026-06-02", 0.0, 3000.0, 0.0))
        
        conn.commit()
        conn.close()

        # 3. Simulate file upload of a PDF statement
        import io
        pdf_data = b"%PDF-1.4 mock pdf data"
        file_payload = {"file": ("statement.pdf", io.BytesIO(pdf_data), "application/pdf")}
        form_payload = {"bank_name": "SBI"}

        response = self.client.post(
            "/api/reconcile-bank-statement",
            files=file_payload,
            data=form_payload
        )

        self.assertEqual(response.status_code, 200)
        res_json = response.json()
        self.assertEqual(res_json["status"], "ok")
        self.assertIn("matched_hashes", res_json)
        self.assertIn("missing_drops", res_json)

        # There should be 1 matched hash (for Paytm 4500) and 1 missing drop (for UPI 3000)
        self.assertEqual(len(res_json["matched_hashes"]), 1)
        self.assertEqual(len(res_json["missing_drops"]), 1)
        
        # Verify the missing drop detail
        missing = res_json["missing_drops"][0]
        self.assertEqual(missing["diary_date"], "2026-06-02")
        self.assertEqual(missing["diary_amount"], 3000.0)

        # Assert database state
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Check that bank statement credits were inserted
        cursor.execute("SELECT COUNT(*) FROM bank_statement_credits")
        self.assertEqual(cursor.fetchone()[0], 2)
        
        # Check that digital_settlement_status has been populated
        # 'Paytm Drop' on 2026-06-01 should be SETTLED_IN_BANK
        cursor.execute("""
            SELECT settlement_status FROM digital_settlement_status 
            WHERE diary_date = '2026-06-01' AND source_label = 'Paytm Drop'
        """)
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "SETTLED_IN_BANK")
        
        # 'UPI Tender' on 2026-06-02 should be UNSETTLED_MISSING_CASH
        cursor.execute("""
            SELECT settlement_status FROM digital_settlement_status 
            WHERE diary_date = '2026-06-02' AND source_label = 'UPI Tender'
        """)
        row2 = cursor.fetchone()
        self.assertIsNotNone(row2)
        self.assertEqual(row2[0], "UNSETTLED_MISSING_CASH")
        
        conn.close()

if __name__ == "__main__":
    unittest.main()


