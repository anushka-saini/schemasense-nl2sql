# Worked failure examples

Real failures from the dev split (latest run per version). The error label is Claude's draft and needs a team check (review/labels_review.csv). "Fixed by" = the first later version from which every version passes this question.

## 1. S03 (simple), failed in v0

**Question:** How many orders were canceled?

**v0 answer** (status `blocked`, comments are not allowed):
```sql
/* 1️⃣  Simple case: a single Orders table with a status column */
SELECT COUNT(*) AS cancelled_orders
FROM   Orders
WHERE  status = 'canceled';      -- or 'Cancelled', 'CANCELED', etc.
```

**Draft error label:** `format_failure`

**Gold:**
```sql
SELECT COUNT(*) FROM orders WHERE order_status = 'canceled'
```

**Across versions:** v0 fail · v1 pass · v2 pass · v3 pass · v4 pass

**Fixed by v1:** Baseline instructions (one SELECT, never write, CANNOT_ANSWER, SQL then a one-line explanation) + table and column names.

v1 answer:
```sql
SELECT COUNT(*) AS canceled_orders
FROM orders
WHERE order_status = 'canceled';
```

_Source: logs/results_v0_dev_20261108-142653.csv_

## 2. F01 (fanout), failed in v1

**Question:** For delivered orders purchased in 2018, what were the total freight value and the total amount paid?

**v1 answer** (status `ok`):
```sql
SELECT 
    SUM(oi.freight_value) AS total_freight,
    SUM(op.payment_value) AS total_paid
FROM orders o
JOIN order_items oi      ON o.order_id = oi.order_id
JOIN order_payments op   ON o.order_id = op.order_id
WHERE o.order_status = 'delivered'
  AND strftime('%Y', o.order_purchase_timestamp) = '2018';
```

**Draft error label:** `fanout_double_count`

**Gold:**
```sql
WITH f AS (SELECT order_id, SUM(freight_value) AS freight FROM order_items GROUP BY order_id),
p AS (SELECT order_id, SUM(payment_value) AS paid FROM order_payments GROUP BY order_id)
SELECT ROUND((SELECT SUM(f.freight) FROM f JOIN orders o ON f.order_id = o.order_id
               WHERE o.order_status = 'delivered' AND strftime('%Y', o.order_purchase_timestamp) = '2018'), 2),
       ROUND((SELECT SUM(p.paid) FROM p JOIN orders o ON p.order_id = o.order_id
               WHERE o.order_status = 'delivered' AND strftime('%Y', o.order_purchase_timestamp) = '2018'), 2)
```

**Across versions:** v0 fail · v1 fail · v2 pass · v3 pass · v4 pass

**Fixed by v2:** Real CREATE TABLE text + join keys + business rules (unique customers, fan-out, delivered = sales, revenue, English categories, text dates, city/state formats).

v2 answer:
```sql
WITH item_freight AS (
    SELECT order_id,
           SUM(freight_value) AS freight_sum
    FROM order_items
    GROUP BY order_id
),
order_paid AS (
    SELECT order_id,
           SUM(payment_value) AS paid_sum
    FROM order_payments
    GROUP BY order_id
)
SELECT COALESCE(SUM(i.freight_sum), 0) AS total_freight_value,
       COALESCE(SUM(p.paid_sum), 0)   AS total_amount_paid
FROM orders o
LEFT JOIN item_freight i USING (order_id)
LEFT JOIN order_paid   p USING (order_id)
WHERE o.order_status = 'delivered'
  AND strftime('%Y', o.order_purchase_timestamp) = '2018';
```

_Source: logs/results_v1_dev_20261108-142654.csv_

## 3. M04 (ambiguous), failed in v1

**Question:** What is the best-selling product category?

**v1 answer** (status `ok`):
```sql
SELECT 
    p.product_category_name,
    COUNT(*) AS items_sold
FROM order_items AS oi
JOIN products AS p
    ON oi.product_id = p.product_id
GROUP BY p.product_category_name
ORDER BY items_sold DESC
LIMIT 1;
```

**Draft error label:** `language_mismatch`

**Gold:**
```sql
SELECT t.product_category_name_english FROM order_items i JOIN orders o ON i.order_id = o.order_id
JOIN products p ON i.product_id = p.product_id
JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name
WHERE o.order_status = 'delivered' GROUP BY t.product_category_name_english ORDER BY COUNT(*) DESC LIMIT 1
```

**Across versions:** v0 fail · v1 fail · v2 pass · v3 pass · v4 pass

**Fixed by v2:** Real CREATE TABLE text + join keys + business rules (unique customers, fan-out, delivered = sales, revenue, English categories, text dates, city/state formats).

v2 answer:
```sql
WITH delivered_orders AS (
    SELECT o.order_id, oi.product_id, oi.price
    FROM orders o
    JOIN order_items oi ON o.order_id = oi.order_id
    WHERE o.order_status = 'delivered'
)
SELECT pct.product_category_name_english AS best_selling_category,
       SUM(do.price) AS total_revenue
FROM delivered_orders do
JOIN products p ON do.product_id = p.product_id
JOIN product_category_name_translation pct 
     ON p.product_category_name = pct.product_category_name
GROUP BY pct.product_category_name_english
ORDER BY total_revenue DESC
LIMIT 1;
```

_Source: logs/results_v1_dev_20261108-142654.csv_

## 4. A10 (aggregation), failed in v2

**Question:** How many customers have placed more than one order?

**v2 answer** (status `ok`):
```sql
WITH order_counts AS (
    SELECT
        customer_id,
        COUNT(*) AS order_cnt
    FROM orders
    GROUP BY customer_id
)
SELECT COUNT(DISTINCT c.customer_unique_id) AS customers_multiple_orders
FROM order_counts oc
JOIN customers c ON c.customer_id = oc.customer_id
WHERE oc.order_cnt > 1;
```

**Draft error label:** `wrong_grain`

**Gold:**
```sql
SELECT COUNT(*) FROM (SELECT c.customer_unique_id FROM orders o JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.customer_unique_id HAVING COUNT(*) > 1)
```

**Across versions:** v0 fail · v1 fail · v2 fail · v3 fail · v4 fail

**Not fixed by any later version.**

_Source: logs/results_v2_dev_20261108-142808.csv_

## 5. R02 (refuse), failed in v2

**Question:** What is the profit margin for each product category?

**v2 answer** (status `ok`):
```sql
WITH delivered_orders AS (
    SELECT order_id
    FROM orders
    WHERE order_status = 'delivered'
)
SELECT pct.product_category_name_english AS category,
       ROUND( (SUM(oi.price) - SUM(oi.freight_value)) * 1.0 /
              NULLIF(SUM(oi.price), 0), 4) AS profit_margin
FROM order_items AS oi
JOIN delivered_orders AS do      ON oi.order_id = do.order_id
JOIN products AS p               ON oi.product_id = p.product_id
JOIN product_category_name_translation AS pct
                                 ON p.product_category_name = pct.product_category_name
GROUP BY pct.product_category_name_english;
```

**Draft error label:** `answered_unanswerable`

**Gold:**
```sql
(should be refused)
```

**Across versions:** v0 fail · v1 pass · v2 fail · v3 fail · v4 fail

**Not fixed by any later version.**

_Source: logs/results_v2_dev_20261108-142808.csv_

