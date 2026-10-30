# load the olist csvs from data/raw into data/olist.db
# table name = file name without "olist_" and "_dataset"
# column names are kept exactly as in the csv files (even the typos like "lenght")

import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "olist.db"

FILES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "product_category_name_translation": "product_category_name_translation.csv",
}

# columns that look like numbers but are codes -> keep as text (zip prefixes have leading zeros)
TEXT_COLS = [
    "customer_zip_code_prefix",
    "seller_zip_code_prefix",
    "geolocation_zip_code_prefix",
]

# whole-number columns that can have blanks -> nullable int so they don't become 12.0
INT_COLS = [
    "order_item_id", "payment_sequential", "payment_installments", "review_score",
    "product_name_lenght", "product_description_lenght", "product_photos_qty",
    "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm",
]

# timestamp columns -> stored as TEXT 'YYYY-MM-DD HH:MM:SS' so sqlite date functions work
DATE_COLS = [
    "shipping_limit_date",
    "review_creation_date", "review_answer_timestamp",
    "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
    "order_delivered_customer_date", "order_estimated_delivery_date",
]

# indexes on join keys so queries don't time out (geolocation is ~1M rows)
INDEXES = [
    ("orders", "order_id"), ("orders", "customer_id"),
    ("order_items", "order_id"), ("order_items", "product_id"), ("order_items", "seller_id"),
    ("order_payments", "order_id"), ("order_reviews", "order_id"),
    ("customers", "customer_id"), ("customers", "customer_unique_id"),
    ("products", "product_id"), ("sellers", "seller_id"),
    ("geolocation", "geolocation_zip_code_prefix"),
]


def read_csv(path):
    # utf-8-sig strips the BOM in the translation file, harmless for the others
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={c: str for c in TEXT_COLS})
    for c in df.columns:
        if c in INT_COLS:
            df[c] = df[c].astype("Int64")
        elif c in DATE_COLS:
            d = pd.to_datetime(df[c], format="%Y-%m-%d %H:%M:%S", errors="raise")
            df[c] = d.dt.strftime("%Y-%m-%d %H:%M:%S").where(d.notna(), None)
    return df


def main():
    missing = [f for f in FILES.values() if not (RAW / f).exists()]
    if missing:
        print("Missing csv files in data/raw/:")
        for f in missing:
            print("  ", f)
        raise SystemExit(1)

    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)

    for name, fname in FILES.items():
        df = read_csv(RAW / fname)
        df.to_sql(name, con, index=False)
        print(f"{name:36s} {len(df):>9,} rows")

    for t, c in INDEXES:
        con.execute(f"CREATE INDEX idx_{t}_{c} ON {t}({c})")
    con.commit()

    # double check counts from the db itself
    print("\nrow counts read back from olist.db:")
    for name in FILES:
        n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"{name:36s} {n:>9,}")
    con.close()
    print(f"\nsaved {DB}")


if __name__ == "__main__":
    main()
