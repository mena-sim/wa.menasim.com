"""Diagnostic: show what WooCommerce returns for an order and what the agent extracts.

Run on the server from the repo root:

    ./scripts/inspect-order 105382 hello@menasim.com
    .venv/bin/python3 scripts/inspect_order.py 105382 hello@menasim.com
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_VENV_PY = ROOT / ".venv" / "bin" / "python3"
if _VENV_PY.is_file() and Path(sys.executable).resolve() != _VENV_PY.resolve():
    os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

# Make the app package importable when run as `python scripts/inspect_order.py`.
sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.services.providers.woocommerce import WooCommerceClient  # noqa: E402


def main() -> None:
    order_number = sys.argv[1] if len(sys.argv) > 1 else None
    email = sys.argv[2] if len(sys.argv) > 2 else None
    if not order_number:
        print("Usage: python scripts/inspect_order.py <order_number> [email]")
        return

    init_db()
    db = SessionLocal()
    try:
        wc = WooCommerceClient(db)
        print(f"WooCommerce enabled: {wc.enabled}")
        print(f"Configured QR keys   : {wc.qr_keys}")
        print(f"Configured ICCID keys: {wc.iccid_keys}")
        print(f"Configured status    : {wc.status_keys}")
        if not wc.enabled:
            print("WooCommerce is not configured (base_url/key/secret missing).")
            return

        orders = wc.find_orders(order_number=order_number, email=email)
        print(f"\nOrders returned: {len(orders)}")
        for order in orders[:2]:
            summary = wc.summarize_order(order)
            print("\n--- ORDER SUMMARY ---")
            for k, v in summary.items():
                print(f"  {k}: {v}")

            print("\n--- ORDER-LEVEL meta keys ---")
            for m in order.get("meta_data") or []:
                if isinstance(m, dict):
                    val = str(m.get("value"))
                    val = (val[:80] + "…") if len(val) > 80 else val
                    print(f"  {m.get('key')} = {val}")

            print("\n--- LINE-ITEM meta keys ---")
            for li in order.get("line_items") or []:
                print(f"  line item: {li.get('name')}")
                for m in li.get("meta_data") or []:
                    if isinstance(m, dict):
                        val = str(m.get("value"))
                        val = (val[:80] + "…") if len(val) > 80 else val
                        print(f"    {m.get('key')} = {val}")

            print("\n--- extract_esim() RESULT ---")
            print(f"  {wc.extract_esim(order)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
