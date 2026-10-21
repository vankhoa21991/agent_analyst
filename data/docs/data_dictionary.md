# Olist data dictionary

Public e-commerce database from Brazilian marketplace Olist (Kaggle,
CC BY-NC-SA). 99,441 orders placed between 2017 and 2018, 112,650 order
items, 32,951 products, 3,095 sellers, 99,441 customers.

All timestamps are stored as TEXT in `YYYY-MM-DD HH:MM:SS` format (Brazil
time). Missing values are NULL.

## orders

One row per order. A customer can place several orders.

- `order_id` (TEXT): unique order identifier. Primary key.
- `customer_id` (TEXT): foreign key to customers.customer_id.
- `order_status` (TEXT): one of `delivered`, `shipped`, `canceled`,
  `unavailable`, `processing`, `invoiced`, `created`, `approved`.
  `delivered` means the customer received the package.
- `order_purchase_timestamp` (TEXT): when the customer placed the order.
- `order_approved_at` (TEXT): when the order was approved for processing.
  NULL for many old and canceled orders.
- `order_delivered_carrier_date` (TEXT): when the package was handed to the
  logistics carrier.
- `order_delivered_customer_date` (TEXT): when the customer received the
  package. NULL unless the order was actually delivered.
- `order_estimated_delivery_date` (TEXT): the delivery date promised to the
  customer at purchase time.

## order_items

One row per item in an order. An order can contain several items from
different sellers.

- `order_item_id` (INTEGER): 1..n within the order.
- `order_id` (TEXT): foreign key to orders.order_id.
- `product_id` (TEXT): foreign key to products.product_id.
- `seller_id` (TEXT): foreign key to sellers.seller_id.
- `shipping_limit_date` (TEXT): the date by which the seller had to hand the
  item to the carrier.
- `price` (REAL): unit price of the item in Brazilian reais (BRL), excluding
  freight.
- `freight_value` (REAL): shipping cost charged to the customer for this
  item, in BRL.

## order_payments

One row per payment method used on an order. An order can be paid with
several methods (e.g. part credit card, part voucher).

- `order_id` (TEXT): foreign key to orders.order_id.
- `payment_sequential` (INTEGER): 1..n within the order.
- `payment_type` (TEXT): `credit_card`, `debit_card`, `boleto`,
  `voucher`, `not_informed`.
- `payment_installments` (INTEGER): number of installments (1 for non-card).
- `payment_value` (REAL): amount paid with this method, in BRL. The sum of
  payment_value over an order equals the order total (items + freight).

## order_reviews

One row per review. Most orders have one review; some have none, a few have
several.

- `review_id` (TEXT): unique review identifier.
- `order_id` (TEXT): foreign key to orders.order_id.
- `review_score` (INTEGER): 1 to 5, where 5 is the best.
- `review_comment_title` (TEXT): short title, mostly NULL.
- `review_comment_message` (TEXT): free text in Brazilian Portuguese, mostly NULL.
- `review_creation_date` (TEXT): when the review was written.
- `review_answer_timestamp` (TEXT): when the company answered the review, if at all.

## customers

One row per customer account.

- `customer_id` (TEXT): unique per order account. A person can have several
  customer_id values.
- `customer_unique_id` (TEXT): identifies the natural person; use this to
  count distinct people rather than customer_id.
- `customer_zip_code_prefix` (INTEGER): first digits of the home zip code.
- `customer_city` (TEXT): city of the customer.
- `customer_state` (TEXT): Brazilian state, e.g. `SP`, `RJ`, `MG`.

## products

One row per product listing.

- `product_id` (TEXT): unique product identifier.
- `product_category_name` (TEXT): category in Portuguese, e.g.
  `relogios`, `bebas`, `telefonia`. Translate via
  product_category_name_translation.
- `product_name_lenght` (INTEGER): length of the product name (spelling as in
  the source data).
- `product_description_lenght` (INTEGER): length of the product description.
- `product_photos_qty` (INTEGER): number of photos attached to the listing.
- `product_weight_g` (INTEGER): weight in grams.
- `product_length_cm`, `product_height_cm`, `product_width_cm` (INTEGER):
  package dimensions in centimeters.

## sellers

One row per seller.

- `seller_id` (TEXT): unique seller identifier.
- `seller_zip_code_prefix` (INTEGER): first digits of the seller zip code.
- `seller_city` (TEXT): city of the seller.
- `seller_state` (TEXT): Brazilian state of the seller.

## geolocation

One row per zip code prefix observed in the data (customers and sellers).
Use it to attach latitude/longitude to a zip code prefix.

- `geolocation_zip_code_prefix` (INTEGER): zip code prefix.
- `geolocation_lat` (REAL): latitude.
- `geolocation_lng` (REAL): longitude.
- `geolocation_city` (TEXT): city.
- `geolocation_state` (TEXT): Brazilian state.

## product_category_name_translation

Maps the Portuguese category names used in products to English.

- `product_category_name` (TEXT): Portuguese category name (foreign key to
  products.product_category_name).
- `product_category_name_english` (TEXT): English translation.
