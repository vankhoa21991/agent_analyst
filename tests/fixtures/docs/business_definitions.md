# Business Definitions (fixture)

## Active seller
A seller is active if they have at least one delivered order in the
last 90 days of the data.

## Late delivery
An order is late if order_delivered_customer_date is more than 5 days
after order_approved_at.

## Revenue
Revenue is the sum of price over delivered orders (freight excluded).

## Refund
A refund is an order with order_status of canceled or unavailable.
