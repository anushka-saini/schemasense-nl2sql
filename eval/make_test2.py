# one-off: writes eval/test2_set.json (20 new questions for the post-hoc v5 check). refuses to overwrite.
import json
from pathlib import Path

ROOT = Path(r"C:\Users\Anushka\OneDrive\Desktop\nl-sql")
OUT = ROOT / "eval" / "test2_set.json"
D = "order_status = 'delivered'"


def q(id, tier, question, gold, alt=(), refuse_ok=False, notes=""):
    return {"id": id, "tier": tier, "question": question, "gold_sql": gold, "alt_sql": list(alt),
            "expected": "REFUSE" if gold is None else None, "row_count": None, "refuse_ok": refuse_ok,
            "unsafe": False, "split": "test2", "verified": False,
            "notes": ("Drafted by Claude on 2026-11-09 after prompts/v5.txt was written; not tuned on. " + notes).strip()}


ITEMS = [
    # simple
    q("T2S01", "simple", "How many customers live in the state of Bahia?",
      "SELECT COUNT(DISTINCT customer_unique_id) FROM customers WHERE customer_state = 'BA'",
      notes="Customers = unique people (business rule A, same as M02/S10). State is a 2-letter code."),
    q("T2S02", "simple", "How many products have at least 5 photos?",
      "SELECT COUNT(*) FROM products WHERE product_photos_qty >= 5"),
    q("T2S03", "simple", "How many reviews include a written comment message?",
      "SELECT COUNT(*) FROM order_reviews WHERE review_comment_message IS NOT NULL",
      alt=["SELECT COUNT(*) FROM order_reviews WHERE review_comment_message IS NOT NULL AND TRIM(review_comment_message) <> ''"],
      notes="9 messages are only spaces; both readings accepted."),
    # join
    q("T2J01", "join", "How many delivered orders were bought by customers living in Belo Horizonte?",
      f"SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
      f"WHERE o.{D} AND c.customer_city = 'belo horizonte'",
      notes="City is lowercase without accents."),
    q("T2J02", "join", "Which 3 product categories have the most products listed? Use English category names.",
      "SELECT t.product_category_name_english FROM products p JOIN product_category_name_translation t "
      "ON p.product_category_name = t.product_category_name GROUP BY t.product_category_name_english "
      "ORDER BY COUNT(*) DESC LIMIT 3",
      notes="Counts products (catalogue), no order status involved."),
    q("T2J03", "join", "How many items were sold in delivered orders to customers in the state of Santa Catarina?",
      f"SELECT COUNT(*) FROM order_items i JOIN orders o ON i.order_id = o.order_id "
      f"JOIN customers c ON o.customer_id = c.customer_id WHERE o.{D} AND c.customer_state = 'SC'",
      notes="One order_items row = one unit sold."),
    # aggregation
    q("T2A01", "aggregation", "What is the average weight in grams of products in the furniture_decor category?",
      "SELECT ROUND(AVG(p.product_weight_g), 2) FROM products p JOIN product_category_name_translation t "
      "ON p.product_category_name = t.product_category_name WHERE t.product_category_name_english = 'furniture_decor'"),
    q("T2A02", "aggregation", "How many orders were placed on each day of the week? Use 0 for Sunday.",
      "SELECT strftime('%w', order_purchase_timestamp) AS dow, COUNT(*) FROM orders GROUP BY dow",
      notes="All statuses (orders placed, not sales)."),
    q("T2A03", "aggregation", "On average, how many hours passed between purchase and approval, for orders that have an approval time?",
      "SELECT ROUND(AVG((julianday(order_approved_at) - julianday(order_purchase_timestamp)) * 24), 2) "
      "FROM orders WHERE order_approved_at IS NOT NULL",
      notes="All statuses with an approval time."),
    # fan-out
    q("T2F01", "fanout", "What is the total item revenue from delivered orders that were paid at least partly by boleto?",
      f"SELECT ROUND(SUM(i.price), 2) FROM order_items i JOIN orders o ON i.order_id = o.order_id "
      f"WHERE o.{D} AND o.order_id IN (SELECT order_id FROM order_payments WHERE payment_type = 'boleto')",
      notes="Trap: joining order_payments directly repeats items for orders with several payment rows."),
    q("T2F02", "fanout", "How many delivered orders have more than one item and also more than one payment record?",
      f"WITH i AS (SELECT order_id FROM order_items GROUP BY order_id HAVING COUNT(*) > 1), "
      f"p AS (SELECT order_id FROM order_payments GROUP BY order_id HAVING COUNT(*) > 1) "
      f"SELECT COUNT(*) FROM orders o JOIN i ON o.order_id = i.order_id JOIN p ON o.order_id = p.order_id WHERE o.{D}",
      notes="Trap: counting rows after joining items and payments counts item x payment pairs, not orders."),
    q("T2F03", "fanout", "Among delivered orders paid by credit card, what percentage of reviews gave 5 stars?",
      f"SELECT ROUND(100.0 * SUM(r.review_score = 5) / COUNT(*), 2) FROM order_reviews r JOIN orders o ON r.order_id = o.order_id "
      f"WHERE o.{D} AND o.order_id IN (SELECT order_id FROM order_payments WHERE payment_type = 'credit_card')",
      notes="Over review rows. Trap: joining payments repeats reviews for orders with several card payment rows."),
    # ambiguous
    q("T2M01", "ambiguous", "Which city do most of our customers come from?",
      "SELECT customer_city FROM customers GROUP BY customer_city ORDER BY COUNT(DISTINCT customer_unique_id) DESC LIMIT 1",
      alt=["SELECT customer_city FROM customers GROUP BY customer_city ORDER BY COUNT(*) DESC LIMIT 1"],
      refuse_ok=True, notes="Unique people (gold) or customer ids (alt). Refusal accepted."),
    q("T2M02", "ambiguous", "Which state is our biggest market?",
      "SELECT customer_state FROM customers c JOIN orders o ON o.customer_id = c.customer_id "
      "GROUP BY customer_state ORDER BY COUNT(*) DESC LIMIT 1",
      alt=["SELECT customer_state FROM customers GROUP BY customer_state ORDER BY COUNT(DISTINCT customer_unique_id) DESC LIMIT 1",
           f"SELECT c.customer_state FROM order_items i JOIN orders o ON i.order_id = o.order_id JOIN customers c "
           f"ON o.customer_id = c.customer_id WHERE o.{D} GROUP BY c.customer_state ORDER BY SUM(i.price) DESC LIMIT 1",
           f"SELECT c.customer_state FROM order_payments p JOIN orders o ON p.order_id = o.order_id JOIN customers c "
           f"ON o.customer_id = c.customer_id WHERE o.{D} GROUP BY c.customer_state ORDER BY SUM(p.payment_value) DESC LIMIT 1"],
      refuse_ok=True, notes="Vague measure. Gold = most orders; alts = most people, most delivered revenue, most paid. Refusal accepted."),
    # unanswerable (no column for it)
    q("T2R01", "refuse", "How much commission did each seller pay to the marketplace?", None,
      notes="Unanswerable: no commission or fee column."),
    q("T2R02", "refuse", "How many of our customers are older than 60?", None,
      notes="Unanswerable: no age or birth date."),
    q("T2R03", "refuse", "What is the current stock level of each product?", None,
      notes="Unanswerable: no inventory data."),
    q("T2R04", "refuse", "How many times was each product viewed on the website before it was bought?", None,
      notes="Unanswerable: no page-view data."),
    q("T2R05", "refuse", "What discount percentage was applied to each order?", None,
      notes="Unanswerable: no discount column. Treating voucher payments as a discount is an approximation and fails."),
    q("T2R06", "refuse", "What is the return rate for each product category?", None,
      notes="Unanswerable: no returns data. Using canceled or unavailable orders as returns is an approximation and fails."),
]

assert len(ITEMS) == 20 and len({i["id"] for i in ITEMS}) == 20
if OUT.exists():
    raise SystemExit(f"{OUT} already exists, not overwritten")
OUT.write_text(json.dumps(ITEMS, indent=2, ensure_ascii=False), encoding="utf-8")
print("written", OUT)
