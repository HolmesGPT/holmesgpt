// Seed script for eval 228: MongoDB Aggregation Analysis
//
// Creates an "events" collection with 200 documents across 5 services.
// The data is carefully crafted so that:
//   - "payment-processor" has the most errors: 47 total
//   - Its most common error code is TXN-4821: 23 occurrences
//   - Other services have fewer errors (making the answer unambiguous)
//
// Service error breakdown:
//   payment-processor: 47 errors (TXN-4821 x23, TXN-5190 x14, TXN-3007 x10)
//   inventory-sync:    31 errors (INV-8832 x16, INV-2219 x15)
//   user-auth:         25 errors (AUTH-6654 x13, AUTH-7741 x12)
//   notification-hub:  18 errors (NTF-3390 x10, NTF-1127 x8)
//   search-indexer:    12 errors (IDX-9045 x7, IDX-6673 x5)
//
// Remaining 67 documents are non-error events spread across all services.

db.events.drop();

var docs = [];
var baseDate = new Date("2025-12-01T00:00:00Z");

function addMs(d, ms) { return new Date(d.getTime() + ms); }

// Helper: create an error event
function errorEvent(service, code, message, offset) {
  return {
    service: service,
    level: "error",
    error_code: code,
    message: message,
    timestamp: addMs(baseDate, offset * 60000)
  };
}

// Helper: create a non-error event
function infoEvent(service, message, offset) {
  return {
    service: service,
    level: "info",
    message: message,
    timestamp: addMs(baseDate, offset * 60000)
  };
}

var offset = 0;

// --- payment-processor errors: 47 total ---
// TXN-4821 x23
for (var i = 0; i < 23; i++) {
  docs.push(errorEvent("payment-processor", "TXN-4821", "Transaction timeout waiting for bank response", offset++));
}
// TXN-5190 x14
for (var i = 0; i < 14; i++) {
  docs.push(errorEvent("payment-processor", "TXN-5190", "Currency conversion service unavailable", offset++));
}
// TXN-3007 x10
for (var i = 0; i < 10; i++) {
  docs.push(errorEvent("payment-processor", "TXN-3007", "Duplicate transaction ID detected", offset++));
}

// --- inventory-sync errors: 31 total ---
// INV-8832 x16
for (var i = 0; i < 16; i++) {
  docs.push(errorEvent("inventory-sync", "INV-8832", "Warehouse API connection refused", offset++));
}
// INV-2219 x15
for (var i = 0; i < 15; i++) {
  docs.push(errorEvent("inventory-sync", "INV-2219", "Stock count mismatch during reconciliation", offset++));
}

// --- user-auth errors: 25 total ---
// AUTH-6654 x13
for (var i = 0; i < 13; i++) {
  docs.push(errorEvent("user-auth", "AUTH-6654", "Token validation failed - clock skew detected", offset++));
}
// AUTH-7741 x12
for (var i = 0; i < 12; i++) {
  docs.push(errorEvent("user-auth", "AUTH-7741", "LDAP directory unreachable", offset++));
}

// --- notification-hub errors: 18 total ---
// NTF-3390 x10
for (var i = 0; i < 10; i++) {
  docs.push(errorEvent("notification-hub", "NTF-3390", "Email relay connection timeout", offset++));
}
// NTF-1127 x8
for (var i = 0; i < 8; i++) {
  docs.push(errorEvent("notification-hub", "NTF-1127", "Push notification quota exceeded", offset++));
}

// --- search-indexer errors: 12 total ---
// IDX-9045 x7
for (var i = 0; i < 7; i++) {
  docs.push(errorEvent("search-indexer", "IDX-9045", "Index shard allocation failed", offset++));
}
// IDX-6673 x5
for (var i = 0; i < 5; i++) {
  docs.push(errorEvent("search-indexer", "IDX-6673", "Document parsing error in bulk ingest", offset++));
}

// --- Non-error (info) events: 67 total, spread across services ---
var services = ["payment-processor", "inventory-sync", "user-auth", "notification-hub", "search-indexer"];
var infoMessages = [
  "Health check passed",
  "Request processed successfully",
  "Cache refreshed",
  "Batch job completed",
  "Connection pool recycled",
  "Config reloaded",
  "Metric export completed"
];
for (var i = 0; i < 67; i++) {
  var svc = services[i % services.length];
  var msg = infoMessages[i % infoMessages.length];
  docs.push(infoEvent(svc, msg, offset++));
}

db.events.insertMany(docs);

// Verify counts
var totalErrors = db.events.countDocuments({ level: "error" });
var ppErrors = db.events.countDocuments({ level: "error", service: "payment-processor" });
var txn4821 = db.events.countDocuments({ error_code: "TXN-4821" });
print("Total events: " + db.events.countDocuments({}));
print("Total errors: " + totalErrors);
print("payment-processor errors: " + ppErrors);
print("TXN-4821 count: " + txn4821);
