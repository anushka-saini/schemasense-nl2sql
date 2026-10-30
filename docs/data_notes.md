# Data notes (Olist on SQLite)

Every note below was checked with a query against `data/olist.db`.
The check id (C01, C02, ...) points to the exact SQL and its output in `logs/data_checks.txt`
(re-create it with `python src/data_checks.py`).

Status of this file: **verified by query, waiting for team review.**
The "Rule for prompts" lines are suggestions for v2 business rules. Please confirm or edit them before v2 is built.

## How the database was built

- `python src/load_db.py` loads the 9 CSVs from `data/raw/`. Each table is named after its file without `olist_` and `_dataset`.
- Column names are kept exactly as in the CSVs. That includes the typos `product_name_lenght` and `product_description_lenght` in `products`.
- Type fixes made while loading:
  - zip code prefixes are stored as TEXT, so leading zeros are kept (`01003`, not `1003`). Verified by query: C27.
  - count-like columns (`order_item_id`, `review_score`, `payment_installments`, the product size and weight columns) are INTEGER, not float.
  - the BOM at the start of `product_category_name_translation.csv` is stripped, so the first column is really called `product_category_name`.
  - timestamps are stored as TEXT in `YYYY-MM-DD HH:MM:SS` format (see note 5).
- Indexes were added on the join keys. These are not shown in the CREATE TABLE text.
- No PRIMARY KEY or FOREIGN KEY constraints are declared, so the schema text does not show the relationships. They are listed in note 13.

Row counts after loading (printed by `load_db.py`):

| table | rows |
|---|---:|
| customers | 99,441 |
| geolocation | 1,000,163 |
| order_items | 112,650 |
| order_payments | 103,886 |
| order_reviews | 99,224 |
| orders | 99,441 |
| products | 32,951 |
| sellers | 3,095 |
| product_category_name_translation | 71 |

## Notes

### 1. `customer_id` is one per order. `customer_unique_id` is the real person.
- `customers` has 99,441 rows and 99,441 distinct `customer_id`, but only 96,096 distinct `customer_unique_id`. *Verified by query: C01.*
- `orders` has 99,441 orders and 99,441 distinct `customer_id`, so every order has its own `customer_id`. *Verified by query: C02.*
- Counted by `customer_unique_id`: 93,099 people placed 1 order, 2,745 placed 2, and one person placed 17. *Verified by query: C03.*
- **Rule for prompts:** to count customers or find repeat buyers, use `COUNT(DISTINCT customers.customer_unique_id)`. `COUNT(DISTINCT customer_id)` just counts orders.

### 2. `order_status` values
*Verified by query: C04.*

| order_status | orders |
|---|---:|
| delivered | 96,478 |
| shipped | 1,107 |
| canceled | 625 |
| unavailable | 609 |
| invoiced | 314 |
| processing | 301 |
| created | 5 |
| approved | 2 |

- All values are lowercase. Canceled is spelled `canceled` (one L).
- **Rule for prompts (team to decide):** questions about sales, revenue or delivery times should probably filter `order_status = 'delivered'`. Whether to also exclude only `canceled` and `unavailable` is a definition choice. We need to pick one and write the gold SQL to match it.

### 3. Orders with no items or no payments
- 775 orders have no row in `order_items`: unavailable 603, canceled 164, created 5, invoiced 2, shipped 1. *Verified by query: C09.*
- 1 order (`bfbd0f9bdef84302105ad712db648a6c`, status delivered) has no row in `order_payments`. *Verified by query: C34.*
- So an INNER JOIN from `orders` to `order_items` drops 775 orders. That matters for questions like "how many orders" if the SQL joins first.

### 4. Every foreign key value has a match
- No `order_items.product_id` without a product, no `order_items.seller_id` without a seller, no `orders.customer_id` without a customer, and no payment without an order (all 0). *Verified by query: C32.*
- `product_id` and `seller_id` are unique in `products` and `sellers`. *Verified by query: C33.*

### 5. Timestamps are TEXT `YYYY-MM-DD HH:MM:SS`
- `typeof(order_purchase_timestamp)` is `text` for all 99,441 rows. Range: `2016-09-04 21:15:19` to `2018-10-17 17:30:18`. *Verified by query: C05.*
- `strftime('%Y-%m', ...)`, `date()` and `julianday()` work directly on these columns (C06 groups by `strftime`).
- Date-only columns (`order_estimated_delivery_date`, `review_creation_date`) still have a `00:00:00` time part.
- **Rule for prompts:** use `strftime` / `date()` / `julianday()`. For delivery days use `julianday(order_delivered_customer_date) - julianday(order_purchase_timestamp)`.

### 6. The data covers Sep 2016 to Oct 2018, and the edges are almost empty
*Verified by query: C06.*
- 2016-09: 4 orders, 2016-10: 324, **2016-11: no orders at all**, 2016-12: 1.
- 2018-09: 16 orders, 2018-10: 4. The last full month is 2018-08 (6,512 orders).
- **Rule for prompts:** "last month" or "this year" cannot mean relative to today. The data ends in Oct 2018. This is a good source of ambiguous test questions.

### 7. Missing timestamps depend on status
*Verified by query: C07.*
- Non-delivered statuses mostly have NULL `order_delivered_customer_date` (shipped 1,107 of 1,107 NULL).
- Even among `delivered` orders, 8 have NULL `order_delivered_customer_date` and 14 have NULL `order_approved_at`.
- 6 `canceled` orders do have a delivered date (625 canceled, 619 NULL).
- **Rule for prompts:** delivery-time queries need `order_delivered_customer_date IS NOT NULL` as well as the status filter.

### 8. Joining `order_items` to `order_payments` duplicates rows (fan-out)
Both tables can have several rows per order, and the only link between them is `order_id`. Joining them gives every item × payment combination.

- Rows per order: 88,863 orders have 1 item, 7,516 have 2, 1,322 have 3 (C08 lists only the 1-to-10 groups; the maximum was not checked). *Verified by query: C08.* For payments, 96,479 orders have 1 payment row, 2,382 have 2, some have more. *Verified by query: C10.*
- **Real example**, order `03ecec245220b63fd7f68c1737ba99ba` (C12 to C15):
  - `order_items`: 2 rows, prices 189.00 + 109.90 = **298.90**
  - `order_payments`: 2 rows, credit_card 319.34 + voucher 56.39 = **375.73**
  - joined: 2 × 2 = **4 rows**, so `SUM(price)` = **597.80** and `SUM(payment_value)` = **751.46**. Both are exactly doubled.
- **Whole table** (C16):
  - correct `SUM(payment_value)` from `order_payments` = **16,008,872.12**
  - after joining `order_items` = **20,308,134.71** (+26.9%)
  - correct `SUM(price)` from `order_items` = **13,591,643.70**
  - after joining `order_payments` = **14,209,115.34** (+4.5%)
- **Rule for prompts:** never sum `price` and `payment_value` from one join. Aggregate each table per `order_id` in a subquery or CTE first, then join.

### 9. `price` vs `payment_value`
- `price` is per item row and does not include shipping. `freight_value` is shipping for that item.
- `payment_value` is what was paid, split over `payment_sequential` rows.
- For 98,285 of the 98,665 orders that appear in both tables, `SUM(price + freight_value)` equals `SUM(payment_value)` within 1 cent. For 380 orders they differ. *Verified by query: C17.* (We have not checked why.)
- There is no quantity column. One row in `order_items` is one unit, and `order_item_id` numbers them 1..n inside the order (C08).
- **Rule for prompts (team to decide):** "revenue" is ambiguous. It could be `SUM(price)` (product sales), `SUM(price + freight_value)`, or `SUM(payment_value)` (money paid). Pick one definition for gold SQL and state it in the prompt.

### 10. Payment types
*Verified by query: C11.*
- `credit_card` 76,795 rows, `boleto` 19,784, `voucher` 5,775, `debit_card` 1,529, `not_defined` 3 (all with value 0.00).
- 2 rows have `payment_installments = 0` and 9 rows have `payment_value = 0`. *Verified by query: C31.*
- Values are lowercase with underscores (`credit_card`, not `Credit Card`).

### 11. Category names are in Portuguese
- `products.product_category_name` is Portuguese with underscores, e.g. `cama_mesa_banho` (3,029 products), `esporte_lazer`, `moveis_decoracao`, `beleza_saude`. *Verified by query: C18.*
- The English names are in `product_category_name_translation.product_category_name_english`, e.g. `beleza_saude` → `health_beauty`, `artes` → `art`. *Verified by query: C19.*
- `products` has 73 distinct categories but the translation table has only 71 (C21). These have no translation: `pc_gamer` (3 products) and `portateis_cozinha_e_preparadores_de_alimentos` (10 products). 610 products have a NULL category. *Verified by query: C20.*
- **Rule for prompts:** when a user names a category in English, join `product_category_name_translation` and filter on `product_category_name_english`. An INNER JOIN to the translation table silently drops 623 products (610 NULL + 13 untranslated). Use LEFT JOIN if those products should count.

### 12. Reviews: not one per order, and `review_id` is not unique
- 99,224 review rows, 98,410 distinct `review_id`, 98,673 distinct orders. *Verified by query: C22.*
- 543 orders have 2 review rows and 4 orders have 3. *Verified by query: C23.*
- 99,441 − 98,673 = 768 orders have no review at all.
- Scores 1 to 5, with 5 the most common (57,328). Many reviews have no comment text (e.g. 36,774 of the score-5 reviews). *Verified by query: C24.*
- Comment text is Portuguese, e.g. "A entrega foi super rápida e o pendente é lindo!". *Verified by query: C25.*
- **Rule for prompts:** joining reviews to orders can duplicate orders. Average score per order or category is slightly affected.

### 13. Relationships (join keys)
Not declared in the schema. Checked by the match counts in C32.
- `orders.customer_id` = `customers.customer_id`
- `order_items.order_id` = `orders.order_id`
- `order_payments.order_id` = `orders.order_id`
- `order_reviews.order_id` = `orders.order_id`
- `order_items.product_id` = `products.product_id`
- `order_items.seller_id` = `sellers.seller_id`
- `products.product_category_name` = `product_category_name_translation.product_category_name`
- `customers.customer_zip_code_prefix` / `sellers.seller_zip_code_prefix` = `geolocation.geolocation_zip_code_prefix`. This one is not 1-to-1 (see note 14).
- Sellers are linked to orders only through `order_items`. 1,219 orders have 2 sellers and a few have up to 5. *Verified by query: C30.*

### 14. Geolocation has many rows per zip prefix
- 1,000,163 rows but only 19,015 distinct zip prefixes. One prefix has 1,146 rows. *Verified by query: C26.*
- **Rule for prompts:** never join `geolocation` directly to customers or sellers. It multiplies rows. Aggregate it per prefix first, or avoid it.

### 15. City and state text format
- Cities are lowercase, e.g. `sao paulo`, `rio de janeiro`. States are 2-letter uppercase codes, e.g. `SP`, `RJ`. *Verified by query: C28.*
- 27 distinct customer states and 23 seller states. *Verified by query: C29.*
- `customers.customer_city` has no accented names (0 of 4,119). `sellers` has 2 and `geolocation` has 2,085 (C35). In `geolocation` the same city appears both ways: `sao paulo` 135,800 rows and `são paulo` 24,918 rows. *Verified by query: C36.*
- **Rule for prompts:** filter city with lowercase, no accents (`customer_city = 'sao paulo'`). Filter state with the 2-letter code (`customer_state = 'SP'`).
