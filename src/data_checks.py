# runs the queries behind docs/data_notes.md and saves sql + output to logs/data_checks.txt
# so every note can be traced back to a real query

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "olist.db"
OUT = ROOT / "logs" / "data_checks.txt"

# the fan-out example order is picked by query C12 and then reused in C13
CHECKS = [
    ("C01 customer_id unique in customers? and how many real people (customer_unique_id)",
     """SELECT COUNT(*) AS n_rows, COUNT(DISTINCT customer_id) AS n_customer_id,
               COUNT(DISTINCT customer_unique_id) AS n_customer_unique_id
        FROM customers"""),

    ("C02 is customer_id one-per-order in orders?",
     """SELECT COUNT(*) AS n_orders, COUNT(DISTINCT order_id) AS n_order_id,
               COUNT(DISTINCT customer_id) AS n_customer_id
        FROM orders"""),

    ("C03 people (customer_unique_id) with more than one order",
     """SELECT n_orders, COUNT(*) AS n_people FROM (
            SELECT c.customer_unique_id, COUNT(*) AS n_orders
            FROM orders o JOIN customers c ON o.customer_id = c.customer_id
            GROUP BY c.customer_unique_id)
        GROUP BY n_orders ORDER BY n_orders"""),

    ("C04 order_status values",
     """SELECT order_status, COUNT(*) AS n FROM orders
        GROUP BY order_status ORDER BY n DESC"""),

    ("C05 how timestamps are stored (type + sample + range)",
     """SELECT typeof(order_purchase_timestamp) AS type, MIN(order_purchase_timestamp) AS min_ts,
               MAX(order_purchase_timestamp) AS max_ts, COUNT(*) AS n
        FROM orders GROUP BY 1"""),

    ("C06 orders per year-month (strftime works on the text dates)",
     """SELECT strftime('%Y-%m', order_purchase_timestamp) AS ym, COUNT(*) AS n
        FROM orders GROUP BY ym ORDER BY ym"""),

    ("C07 null timestamps by status (delivered date is missing for non-delivered orders)",
     """SELECT order_status, COUNT(*) AS n,
               SUM(order_approved_at IS NULL) AS null_approved,
               SUM(order_delivered_carrier_date IS NULL) AS null_carrier,
               SUM(order_delivered_customer_date IS NULL) AS null_delivered
        FROM orders GROUP BY order_status ORDER BY n DESC"""),

    ("C08 items per order (order_item_id is 1..n inside an order, there is no quantity column)",
     """SELECT n_items, COUNT(*) AS n_orders FROM (
            SELECT order_id, COUNT(*) AS n_items FROM order_items GROUP BY order_id)
        GROUP BY n_items ORDER BY n_items LIMIT 10"""),

    ("C09 orders with no rows in order_items, by status",
     """SELECT o.order_status, COUNT(*) AS n
        FROM orders o LEFT JOIN order_items i ON o.order_id = i.order_id
        WHERE i.order_id IS NULL GROUP BY o.order_status ORDER BY n DESC"""),

    ("C10 payment rows per order",
     """SELECT n_pay, COUNT(*) AS n_orders FROM (
            SELECT order_id, COUNT(*) AS n_pay FROM order_payments GROUP BY order_id)
        GROUP BY n_pay ORDER BY n_pay LIMIT 10"""),

    ("C11 payment_type values",
     """SELECT payment_type, COUNT(*) AS n, ROUND(SUM(payment_value), 2) AS total
        FROM order_payments GROUP BY payment_type ORDER BY n DESC"""),

    ("C12 pick an example order with several items AND several payment rows",
     """SELECT i.order_id, i.n_items, p.n_pay FROM
            (SELECT order_id, COUNT(*) AS n_items FROM order_items GROUP BY order_id) i
        JOIN (SELECT order_id, COUNT(*) AS n_pay FROM order_payments GROUP BY order_id) p
            ON i.order_id = p.order_id
        WHERE i.n_items >= 2 AND p.n_pay >= 2
        ORDER BY i.order_id LIMIT 3"""),

    ("C13 fan-out on that order: items x payments rows, naive sums vs correct sums",
     """WITH ex AS (
            SELECT i.order_id FROM
                (SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY order_id) i
            JOIN (SELECT order_id, COUNT(*) AS n FROM order_payments GROUP BY order_id) p
                ON i.order_id = p.order_id
            WHERE i.n >= 2 AND p.n >= 2 ORDER BY i.order_id LIMIT 1)
        SELECT
            (SELECT order_id FROM ex) AS order_id,
            (SELECT COUNT(*) FROM order_items WHERE order_id = (SELECT order_id FROM ex)) AS item_rows,
            (SELECT COUNT(*) FROM order_payments WHERE order_id = (SELECT order_id FROM ex)) AS pay_rows,
            (SELECT COUNT(*) FROM order_items i JOIN order_payments p ON i.order_id = p.order_id
              WHERE i.order_id = (SELECT order_id FROM ex)) AS joined_rows,
            (SELECT ROUND(SUM(price), 2) FROM order_items WHERE order_id = (SELECT order_id FROM ex)) AS true_price_sum,
            (SELECT ROUND(SUM(i.price), 2) FROM order_items i JOIN order_payments p ON i.order_id = p.order_id
              WHERE i.order_id = (SELECT order_id FROM ex)) AS joined_price_sum,
            (SELECT ROUND(SUM(payment_value), 2) FROM order_payments WHERE order_id = (SELECT order_id FROM ex)) AS true_pay_sum,
            (SELECT ROUND(SUM(p.payment_value), 2) FROM order_items i JOIN order_payments p ON i.order_id = p.order_id
              WHERE i.order_id = (SELECT order_id FROM ex)) AS joined_pay_sum"""),

    ("C14 the example order's raw rows (items)",
     """SELECT order_id, order_item_id, product_id, price, freight_value FROM order_items
        WHERE order_id = (
            SELECT i.order_id FROM
                (SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY order_id) i
            JOIN (SELECT order_id, COUNT(*) AS n FROM order_payments GROUP BY order_id) p
                ON i.order_id = p.order_id
            WHERE i.n >= 2 AND p.n >= 2 ORDER BY i.order_id LIMIT 1)"""),

    ("C15 the example order's raw rows (payments)",
     """SELECT order_id, payment_sequential, payment_type, payment_installments, payment_value
        FROM order_payments
        WHERE order_id = (
            SELECT i.order_id FROM
                (SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY order_id) i
            JOIN (SELECT order_id, COUNT(*) AS n FROM order_payments GROUP BY order_id) p
                ON i.order_id = p.order_id
            WHERE i.n >= 2 AND p.n >= 2 ORDER BY i.order_id LIMIT 1)
        ORDER BY payment_sequential"""),

    ("C16 whole-table fan-out: total payment_value correct vs after joining order_items",
     """SELECT
            (SELECT ROUND(SUM(payment_value), 2) FROM order_payments) AS correct_total,
            (SELECT ROUND(SUM(p.payment_value), 2) FROM order_payments p
               JOIN order_items i ON p.order_id = i.order_id) AS joined_total,
            (SELECT ROUND(SUM(price), 2) FROM order_items) AS correct_item_price_total,
            (SELECT ROUND(SUM(i.price), 2) FROM order_items i
               JOIN order_payments p ON p.order_id = i.order_id) AS joined_item_price_total"""),

    ("C17 does price + freight per order equal payment total per order?",
     """WITH a AS (SELECT order_id, SUM(price + freight_value) AS items_total
                   FROM order_items GROUP BY order_id),
             b AS (SELECT order_id, SUM(payment_value) AS paid
                   FROM order_payments GROUP BY order_id)
        SELECT COUNT(*) AS orders_in_both,
               SUM(ABS(a.items_total - b.paid) < 0.01) AS match_within_1_cent,
               SUM(ABS(a.items_total - b.paid) >= 0.01) AS differ
        FROM a JOIN b ON a.order_id = b.order_id"""),

    ("C18 category names: sample from products (language)",
     """SELECT product_category_name, COUNT(*) AS n FROM products
        GROUP BY product_category_name ORDER BY n DESC LIMIT 8"""),

    ("C19 translation table sample",
     """SELECT * FROM product_category_name_translation ORDER BY product_category_name LIMIT 8"""),

    ("C20 product categories with no english translation, and products with NULL category",
     """SELECT COALESCE(p.product_category_name, '<NULL>') AS category, COUNT(*) AS n_products
        FROM products p LEFT JOIN product_category_name_translation t
            ON p.product_category_name = t.product_category_name
        WHERE t.product_category_name IS NULL
        GROUP BY 1 ORDER BY n_products DESC"""),

    ("C21 distinct categories in products vs rows in translation table",
     """SELECT (SELECT COUNT(DISTINCT product_category_name) FROM products) AS cats_in_products,
               (SELECT COUNT(*) FROM product_category_name_translation) AS translation_rows,
               (SELECT COUNT(DISTINCT product_category_name) FROM product_category_name_translation) AS distinct_translated"""),

    ("C22 reviews: is review_id unique? reviews per order",
     """SELECT COUNT(*) AS n_rows, COUNT(DISTINCT review_id) AS n_review_id,
               COUNT(DISTINCT order_id) AS n_orders_with_review
        FROM order_reviews"""),

    ("C23 orders with more than one review row",
     """SELECT n_reviews, COUNT(*) AS n_orders FROM (
            SELECT order_id, COUNT(*) AS n_reviews FROM order_reviews GROUP BY order_id)
        GROUP BY n_reviews ORDER BY n_reviews"""),

    ("C24 review_score distribution and empty comments",
     """SELECT review_score, COUNT(*) AS n,
               SUM(review_comment_message IS NULL) AS no_message
        FROM order_reviews GROUP BY review_score ORDER BY review_score"""),

    ("C25 review comment language sample",
     """SELECT review_comment_message FROM order_reviews
        WHERE review_comment_message IS NOT NULL ORDER BY review_id LIMIT 3"""),

    ("C26 geolocation: rows per zip prefix (joining on zip fans out)",
     """SELECT COUNT(*) AS n_rows, COUNT(DISTINCT geolocation_zip_code_prefix) AS n_prefixes,
               MAX(cnt) AS max_rows_for_one_prefix FROM (
            SELECT geolocation_zip_code_prefix, COUNT(*) AS cnt FROM geolocation
            GROUP BY geolocation_zip_code_prefix) g
        JOIN geolocation USING (geolocation_zip_code_prefix)"""),

    ("C27 zip prefix stored as text with leading zeros",
     """SELECT customer_zip_code_prefix, typeof(customer_zip_code_prefix) AS type, length(customer_zip_code_prefix) AS len
        FROM customers WHERE customer_zip_code_prefix LIKE '0%' ORDER BY customer_zip_code_prefix LIMIT 3"""),

    ("C28 city / state format",
     """SELECT customer_city, customer_state, COUNT(*) AS n FROM customers
        GROUP BY 1, 2 ORDER BY n DESC LIMIT 5"""),

    ("C29 number of distinct states",
     """SELECT COUNT(DISTINCT customer_state) AS customer_states,
               (SELECT COUNT(DISTINCT seller_state) FROM sellers) AS seller_states
        FROM customers"""),

    ("C30 sellers per order",
     """SELECT n_sellers, COUNT(*) AS n_orders FROM (
            SELECT order_id, COUNT(DISTINCT seller_id) AS n_sellers FROM order_items GROUP BY order_id)
        GROUP BY n_sellers ORDER BY n_sellers"""),

    ("C31 odd payment rows (not_defined type, 0 installments, 0 value)",
     """SELECT SUM(payment_type = 'not_defined') AS not_defined,
               SUM(payment_installments = 0) AS zero_installments,
               SUM(payment_value = 0) AS zero_value
        FROM order_payments"""),

    ("C32 does every order_items.product_id / seller_id / orders.customer_id have a match?",
     """SELECT
            (SELECT COUNT(*) FROM order_items i LEFT JOIN products p ON i.product_id = p.product_id
              WHERE p.product_id IS NULL) AS items_no_product,
            (SELECT COUNT(*) FROM order_items i LEFT JOIN sellers s ON i.seller_id = s.seller_id
              WHERE s.seller_id IS NULL) AS items_no_seller,
            (SELECT COUNT(*) FROM orders o LEFT JOIN customers c ON o.customer_id = c.customer_id
              WHERE c.customer_id IS NULL) AS orders_no_customer,
            (SELECT COUNT(*) FROM order_payments p LEFT JOIN orders o ON p.order_id = o.order_id
              WHERE o.order_id IS NULL) AS payments_no_order"""),

    ("C33 product_id / seller_id unique in their tables?",
     """SELECT (SELECT COUNT(*) - COUNT(DISTINCT product_id) FROM products) AS dup_product_id,
               (SELECT COUNT(*) - COUNT(DISTINCT seller_id) FROM sellers) AS dup_seller_id"""),

    ("C34 orders with no payment row",
     """SELECT o.order_id, o.order_status FROM orders o
        LEFT JOIN order_payments p ON o.order_id = p.order_id
        WHERE p.order_id IS NULL"""),

    ("C35 city names with accents / non-ascii characters (customers, sellers, geolocation)",
     """SELECT 'customers' AS tbl, COUNT(DISTINCT customer_city) AS n_cities,
               COUNT(DISTINCT CASE WHEN customer_city GLOB '*[^ -~]*' THEN customer_city END) AS non_ascii
        FROM customers
        UNION ALL
        SELECT 'sellers', COUNT(DISTINCT seller_city),
               COUNT(DISTINCT CASE WHEN seller_city GLOB '*[^ -~]*' THEN seller_city END) FROM sellers
        UNION ALL
        SELECT 'geolocation', COUNT(DISTINCT geolocation_city),
               COUNT(DISTINCT CASE WHEN geolocation_city GLOB '*[^ -~]*' THEN geolocation_city END) FROM geolocation"""),

    ("C36 example of the same city spelled two ways in geolocation",
     """SELECT geolocation_city, COUNT(*) AS n FROM geolocation
        WHERE geolocation_city IN ('sao paulo', 'são paulo') GROUP BY 1"""),
]


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.max_colwidth", 80)
    lines = [f"data checks run at {datetime.now():%Y-%m-%d %H:%M:%S} on {DB.name}", ""]
    for title, sql in CHECKS:
        df = pd.read_sql_query(sql, con)
        lines += ["=" * 80, title, "-" * 80, sql.strip(), "-" * 80, df.to_string(index=False), ""]
    con.close()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
