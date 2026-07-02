"""Diagnostic: show what WooCommerce returns for an order and what the agent extracts.

Run on the server (repo root, venv active):

    python scripts/inspect_order.py 105382 hello@menasim.com

It prints the order summary, every meta key (order-level + line-item), and the
result of extract_esim() — so we can see whether the QR/ICCID are present and
which key holds them.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the app package importable when run as `python scripts/inspect_order.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
