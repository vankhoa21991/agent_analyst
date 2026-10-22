# Data Dictionary (fixture)

## orders
One row per order. Columns: order_id (text), customer_id (text),
order_status (text: created, approved, delivered, canceled, shipped,
invoiced, processing, unavailable), order_approved_at (datetime),
order_delivered_customer_date (datetime, the date the customer
received the order), order_purchase_timestamp (datetime).

## order_items
One row per ordered item. Columns: order_id (text),
order_item_id (int), product_id (text), price (real),
freight_value (real).

## products
One row per product. Columns: product_id (text),
product_category_name (text), product_name_len (int),
product_photos_qty (int).

## customers
One row per customer address. Columns: customer_id (text),
customer_unique_id (text), customer_city (text), customer_state (text).
