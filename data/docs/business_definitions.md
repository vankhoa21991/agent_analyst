# Business definitions

Terms used in everyday questions about this marketplace, and the different
reasonable ways each can be computed. When a question uses one of these terms
without specifying, the agent should ask which meaning is intended (or state
which default it is using).

## revenue

The money the marketplace records for orders. Reasonable interpretations:

1. Sum of `order_items.price` (item prices only, excluding freight).
2. Sum of `order_items.price + order_items.freight_value` (what the customer
   paid for goods and shipping).
3. Sum of `order_payments.payment_value` (what was actually paid, which
   equals 2 for completed payments).

Which orders count is also open: all orders, only `delivered` orders, or all
orders except `canceled`. Default in this project: interpretation 3 over
orders whose status is `delivered`.

## active seller

A seller that is "currently selling". Reasonable interpretations:

1. Sellers with at least one order in the last 30 days.
2. Sellers with at least one order in the last 90 days.
3. Sellers with at least one `delivered` order in the last 12 months.
4. Any seller that ever appears in `order_items` (all-time).

Default in this project: interpretation 2, based on
`orders.order_purchase_timestamp`, counting distinct `seller_id` from
`order_items`.

## late delivery

A delivery that was "late". Reasonable interpretations:

1. `orders.order_delivered_customer_date` later than
   `orders.order_estimated_delivery_date` (the date promised to the customer).
2. `orders.order_delivered_carrier_date` later than
   `order_items.shipping_limit_date` (the seller missed the handover
   deadline to the carrier).

Only orders actually delivered can be late under either reading. Default in
this project: interpretation 1.

## repeat customer

A customer who "comes back". Reasonable interpretations:

1. A `customer_unique_id` with at least 2 orders of any status.
2. A `customer_unique_id` with at least 2 `delivered` orders.
3. A `customer_id` (account, not person) with at least 2 orders.

Default in this project: interpretation 1.

## average order value

Revenue per order. The ambiguity is which revenue definition is used (see
revenue) and whether canceled orders are in the denominator. Default in this
project: revenue per `delivered` order, revenue = interpretation 3.

## cancellation rate

The share of orders that are "canceled". Reasonable interpretations:

1. `canceled` orders divided by all orders.
2. `canceled` orders divided by orders that were at least `approved`.
3. (`canceled` + `unavailable`) orders divided by all orders, since
   `unavailable` is also a form of failure.

Default in this project: interpretation 1.

## seller rating

How well a seller does, judged by reviews. Reasonable interpretations:

1. Average `order_reviews.review_score` over all reviews of the seller's
   items (join order_items to products to order_reviews via order_id).
2. Same, but only reviews with score <= 2 removed as outliers.
3. Share of 5-star reviews for the seller's items.

Default in this project: interpretation 1, sellers with at least 5 reviews.

## fulfillment time

How long an order takes to process. Reasonable interpretations:

1. `order_approved_at` minus `order_purchase_timestamp` (approval time).
2. `order_delivered_carrier_date` minus `order_purchase_timestamp`
   (time until the carrier gets the package).
3. `order_delivered_customer_date` minus `order_purchase_timestamp`
   (end-to-end, which is really delivery time, not fulfillment).

Default in this project: interpretation 2, over delivered orders.

## top category

The "biggest" product category. Reasonable interpretations:

1. Most orders (distinct order_id in order_items for that category).
2. Highest total item price (revenue, interpretation 1 of revenue).
3. Most distinct products listed.

Default in this project: interpretation 1.

## new customer

A customer who is "new". Reasonable interpretations:

1. A `customer_unique_id` whose first-ever order is in the period.
2. A `customer_unique_id` with no order in the 12 months before the period.
3. A `customer_id` account created in the period (not directly in the data).

Default in this project: interpretation 1.
