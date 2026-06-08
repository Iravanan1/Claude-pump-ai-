#!/usr/bin/env python3
"""
Unstructured Margin Expense Categorization and Mapping Module.

Maps raw, handwritten expense descriptions extracted by the AI engine into
formal business ledger accounting heads using a localized keyword dictionary.

Algorithm:
  1. Load expense_categories.json from the same directory.
  2. For each expense, scan party_name + remarks against every keyword list.
  3. On the first case-insensitive substring match, assign that ledger head.
  4. If no keyword matches, assign 'Unclassified Operational Expenses'.
  5. Inject accounting_head into the expense dict (overwrites if already set).

Keyword precedence: first match wins (categories are evaluated in file order).
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("ExpenseMapper")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
CATEGORIES_FILE = os.path.join(BACKEND_DIR, "expense_categories.json")

FALLBACK_CATEGORY = "Unclassified Operational Expenses"

# ── Module-level cache ─────────────────────────────────────────────────────────
_CATEGORIES: Optional[Dict[str, List[str]]] = None


def _load_categories(force_reload: bool = False) -> Dict[str, List[str]]:
    """
    Loads and caches expense_categories.json.
    Returns {accounting_head: [keyword, ...]} mapping.
    """
    global _CATEGORIES
    if _CATEGORIES is not None and not force_reload:
        return _CATEGORIES

    if not os.path.exists(CATEGORIES_FILE):
        logger.warning(
            f"expense_categories.json not found at {CATEGORIES_FILE}. "
            "All expenses will fall back to 'Unclassified Operational Expenses'."
        )
        _CATEGORIES = {}
        return _CATEGORIES

    try:
        with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # Normalise: keys are accounting heads, values are keyword lists
        _CATEGORIES = {head: [str(kw).lower() for kw in keywords]
                       for head, keywords in raw.items()}

        logger.info(
            f"Loaded {len(_CATEGORIES)} accounting heads from expense_categories.json."
        )
    except Exception as e:
        logger.error(f"Failed to load expense_categories.json: {e}")
        _CATEGORIES = {}

    return _CATEGORIES


def reload_categories() -> Dict[str, List[str]]:
    """Force-reloads expense_categories.json from disk (useful after edits)."""
    return _load_categories(force_reload=True)


def get_accounting_head(description: str) -> str:
    """
    Determines the formal accounting head for a single expense description string.

    Matches case-insensitively against all registered keyword lists.
    First-match-wins; falls back to FALLBACK_CATEGORY if nothing matches.

    Parameters
    ----------
    description : str
        Combined description text to classify (party_name + ' ' + remarks).

    Returns
    -------
    str
        Formal accounting ledger head, or 'Unclassified Operational Expenses'.
    """
    if not description or not description.strip():
        return FALLBACK_CATEGORY

    desc_lower = description.lower().strip()
    categories = _load_categories()

    for head, keywords in categories.items():
        for kw in keywords:
            if kw and kw in desc_lower:
                logger.debug(
                    f"Matched expense '{description[:40]}' → '{head}' (keyword: '{kw}')"
                )
                return head

    logger.debug(
        f"No category match for expense '{description[:40]}' → '{FALLBACK_CATEGORY}'"
    )
    return FALLBACK_CATEGORY


def categorize_loose_expenses(
    extracted_expense_array: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Post-processing function that intercepts the raw cash_expenses JSON array
    produced by the AI engine, classifies each entry, and injects an
    'accounting_head' field into every record.

    Unmatched entries receive accounting_head = 'Unclassified Operational Expenses'.

    Parameters
    ----------
    extracted_expense_array : list of dict
        Raw expense entries, each with at minimum:
          - party_name (str)
          - amount (float)
          - remarks (str)

    Returns
    -------
    list of dict
        The same list, with 'accounting_head' field added/overwritten on every entry.
    """
    if not extracted_expense_array:
        return extracted_expense_array

    for expense in extracted_expense_array:
        # Build combined search string from party_name + remarks
        party  = str(expense.get("party_name") or "").strip()
        remark = str(expense.get("remarks")    or "").strip()
        combined = f"{party} {remark}".strip()

        head = get_accounting_head(combined)
        expense["accounting_head"] = head

        if head == FALLBACK_CATEGORY:
            logger.info(
                f"[ExpenseMapper] Unclassified expense: party='{party}' "
                f"remarks='{remark}' amount={expense.get('amount', 0.0)}"
            )

    return extracted_expense_array


def get_all_categories() -> List[str]:
    """Returns the list of all known formal accounting heads (plus the fallback)."""
    cats = list(_load_categories().keys())
    if FALLBACK_CATEGORY not in cats:
        cats.append(FALLBACK_CATEGORY)
    return cats
