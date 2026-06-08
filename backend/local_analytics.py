"""
Minimalist Local Trend Visualization Engine.

Generates premium, dark-mode consistent static PNG charts:
1. volume_trend.png — daily HSD and MS sales volume line curves
2. credit_concentration.png — top 10 outstanding customer credit balances
3. cash_flow_composition.png — gross revenue split by tender vectors
"""

import os
import sqlite3
import logging
from typing import Dict, Any, List

# Enforce matplotlib non-interactive Agg backend before importing pyplot
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Decryptor helper
try:
    from crypto_vault import decrypt_field
except ImportError:
    def decrypt_field(val, return_type=str):
        if val is None:
            return None
        return return_type(val)

logger = logging.getLogger("LocalAnalytics")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(BACKEND_DIR)
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")
CHARTS_DIR = os.path.join(WORKSPACE_DIR, "pump_exports", "charts")


def generate_all_charts(db_path: str = DB_PATH, charts_dir: str = CHARTS_DIR) -> Dict[str, str]:
    """
    Pulls data from SQLite database and compiles three high-resolution static charts.
    Saves PNGs to /pump_exports/charts/.
    
    Returns
    -------
    Dict[str, str] - mapping of chart names to their output PNG file paths
    """
    logger.info(f"Generating analytics charts inside: {charts_dir}...")
    os.makedirs(charts_dir, exist_ok=True)
    
    # Premium Dark-Theme Styling Configurations
    plt.rcParams['figure.facecolor'] = '#121214'
    plt.rcParams['axes.facecolor'] = '#1E1E24'
    plt.rcParams['text.color'] = '#E0E0EB'
    plt.rcParams['axes.labelcolor'] = '#A0A0B2'
    plt.rcParams['xtick.color'] = '#A0A0B2'
    plt.rcParams['ytick.color'] = '#A0A0B2'
    plt.rcParams['font.family'] = 'sans-serif'
    
    paths = {}
    
    # ── Chart 1: Volume Trend Line ──
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT date, total_hsd_liters, total_ms_liters FROM daily_summary ORDER BY date ASC")
        rows = cursor.fetchall()
        conn.close()
        
        dates = [r[0] for r in rows]
        hsd_vols = [float(r[1] or 0.0) for r in rows]
        ms_vols = [float(r[2] or 0.0) for r in rows]
        
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
        
        if dates:
            ax.plot(dates, hsd_vols, color='#1F497D', linewidth=2.5, label='Diesel (HSD)', marker='o', markersize=4)
            ax.plot(dates, ms_vols, color='#FF8C00', linewidth=2.5, label='Petrol (MS)', marker='s', markersize=4)
            
            # Format X-axis ticks (skip intermediate dates if too many days)
            step = max(1, len(dates) // 6)
            ax.set_xticks(dates[::step])
            plt.xticks(rotation=15)
        else:
            ax.text(0.5, 0.5, "No ledger daily summaries recorded yet.", ha='center', va='center', fontsize=12)
            
        ax.set_title("Daily Fuel Sales Volume Trends (Liters)", fontsize=13, fontweight='bold', pad=15, color='#E0E0EB')
        ax.set_ylabel("Volume Sold (Ltrs)", fontsize=10, labelpad=8)
        ax.grid(color='#2E2E38', linestyle='--', alpha=0.5)
        
        # Style spines
        for spine in ['top', 'right']:
            ax.spines[spine].set_visible(False)
        for spine in ['left', 'bottom']:
            ax.spines[spine].set_color('#444452')
            
        ax.legend(facecolor='#1E1E24', edgecolor='#2E2E38', loc='upper left')
        fig.tight_layout()
        
        v_path = os.path.join(charts_dir, "volume_trend.png")
        fig.savefig(v_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
        plt.close(fig)
        paths["volume_trend"] = v_path
        logger.info("Volume Trend line chart generated successfully.")
    except Exception as e:
        logger.error(f"Failed to generate Volume Trend line chart: {e}")
        
    # ── Chart 2: Credit Concentration Bar Chart ──
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT party_name, amount, type FROM ledger_entries")
        rows = cursor.fetchall()
        conn.close()
        
        balances = {}
        for row in rows:
            r_party_enc = row[0]
            r_amount_enc = row[1]
            r_type = row[2]
            
            try:
                party = decrypt_field(r_party_enc, return_type=str)
                amount = decrypt_field(r_amount_enc, return_type=float)
            except Exception:
                party = str(r_party_enc or "")
                amount = float(r_amount_enc or 0.0)
                
            if not party:
                continue
                
            party_clean = party.strip()
            if party_clean not in balances:
                balances[party_clean] = 0.0
                
            if r_type == "udhaar":
                balances[party_clean] += amount
            elif r_type in ("payment", "deposit", "receipt"):
                balances[party_clean] -= amount
            elif amount < 0:
                balances[party_clean] += amount
                
        # Filter for positive outstanding credit balances and sort descending
        outstanding_balances = {k: v for k, v in balances.items() if v > 0.0}
        top_10 = sorted(outstanding_balances.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Sort ascending for horizontal bar chart (so highest is at the top)
        top_10.reverse()
        
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
        
        if top_10:
            customers = [item[0] for item in top_10]
            values = [item[1] for item in top_10]
            
            bars = ax.barh(customers, values, color='#1F497D', height=0.6, edgecolor='#3A6B9C', linewidth=1)
            
            # Label bars with value
            for bar in bars:
                width = bar.get_width()
                ax.text(width + (width * 0.02) + 200, bar.get_y() + bar.get_height()/2, 
                        f"₹{width:,.0f}", ha='left', va='center', fontsize=9, color='#E0E0EB', fontweight='bold')
                
            ax.set_xlim(0, max(values) * 1.18 if values else 1000)
        else:
            ax.text(0.5, 0.5, "No outstanding credit ledger accounts outstanding.", ha='center', va='center', fontsize=12)
            
        ax.set_title("Credit Concentration — Top 10 Outstanding Customer Balances", fontsize=13, fontweight='bold', pad=15, color='#E0E0EB')
        ax.set_xlabel("Outstanding Balance (INR)", fontsize=10, labelpad=8)
        ax.grid(color='#2E2E38', linestyle='--', alpha=0.3, axis='x')
        
        for spine in ['top', 'right']:
            ax.spines[spine].set_visible(False)
        for spine in ['left', 'bottom']:
            ax.spines[spine].set_color('#444452')
            
        fig.tight_layout()
        c_path = os.path.join(charts_dir, "credit_concentration.png")
        fig.savefig(c_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
        plt.close(fig)
        paths["credit_concentration"] = c_path
        logger.info("Credit Concentration bar chart generated successfully.")
    except Exception as e:
        logger.error(f"Failed to generate Credit Concentration bar chart: {e}")
        
    # ── Chart 3: Cash Flow Composition Wheel ──
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales FROM daily_ledger")
        rows = cursor.fetchall()
        conn.close()
        
        cash = sum(float(r[0] or 0.0) for r in rows)
        digital = sum(float((r[1] or 0.0) + (r[2] or 0.0)) for r in rows)
        swipe = sum(float(r[3] or 0.0) for r in rows)
        credit = sum(float(r[4] or 0.0) for r in rows)
        
        totals = cash + digital + swipe + credit
        
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
        
        if totals > 0.0:
            labels = ['Cash', 'Paytm/UPI', 'Swipe Cards', 'Credit Sales']
            sizes = [cash, digital, swipe, credit]
            colors_pie = ['#388E3C', '#0288D1', '#7B1FA2', '#D32F2F']  # Green, Cyan, Purple, Red
            
            # Filter slices > 0 to make pie render beautifully
            filtered_labels = []
            filtered_sizes = []
            filtered_colors = []
            
            for l, s, c in zip(labels, sizes, colors_pie):
                if s > 0.0:
                    filtered_labels.append(l)
                    filtered_sizes.append(s)
                    filtered_colors.append(c)
            
            wedges, texts, autotexts = ax.pie(
                filtered_sizes, 
                labels=filtered_labels, 
                autopct='%1.1f%%',
                startangle=140, 
                colors=filtered_colors,
                textprops=dict(color='#E0E0EB', fontsize=9.5),
                wedgeprops=dict(width=0.4, edgecolor='#121214', linewidth=2.5) # Donut chart visual style
            )
            
            for autotext in autotexts:
                autotext.set_fontweight('bold')
        else:
            # Fallback equal slices for layout visual consistency if empty DB
            labels = ['Cash', 'Paytm/UPI', 'Swipe Cards', 'Credit Sales']
            sizes = [25, 25, 25, 25]
            colors_pie = ['#2E4D2E', '#1C3D4D', '#3D1C4D', '#4D1C1C']
            ax.pie(
                sizes, 
                labels=labels, 
                autopct='%1.1f%%',
                startangle=140, 
                colors=colors_pie,
                textprops=dict(color='#A0A0B2', fontsize=9),
                wedgeprops=dict(width=0.4, edgecolor='#121214', linewidth=2.5)
            )
            ax.text(0.0, 0.0, "No transactions recorded yet\n(Placeholder grid shown)", 
                    ha='center', va='center', fontsize=9.5, color='#FF8C00', fontweight='bold')
            
        ax.set_title("Revenue Cash Flow Composition Vectors", fontsize=13, fontweight='bold', pad=15, color='#E0E0EB')
        fig.tight_layout()
        
        f_path = os.path.join(charts_dir, "cash_flow_composition.png")
        fig.savefig(f_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
        plt.close(fig)
        paths["cash_flow_composition"] = f_path
        logger.info("Cash Flow Composition donut chart generated successfully.")
    except Exception as e:
        logger.error(f"Failed to generate Cash Flow Composition donut chart: {e}")
        
    return paths
