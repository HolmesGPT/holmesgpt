# Kafka Topic and Application Mapping

## Topics and Data Flow

### `finance` Topic
**Purpose**: Order processing pipeline
**Producer**: `orders-app`
**Consumer**: `invoices-app`

- **orders-app** generates customer orders
- **invoices-app** consumes orders

### `payments` Topic
**Purpose**: Payment processing pipeline
**Producer**: `finance-app`
**Consumer**: `accounting-app`

- **finance-app** generates payment transactions (amounts, methods, bank codes)
- **accounting-app** consumes payment

## Topic Flow

```
orders-app → finance → invoices-app
finance-app → payments → accounting-app
```

Both topics operate independently and handle different aspects of the business workflow.

When investigating lag, find and identify the producer and consumer pods and check their logs.
