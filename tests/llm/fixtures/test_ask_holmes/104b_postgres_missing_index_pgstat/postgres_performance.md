# PostgreSQL Performance Investigation

## How to investigate slow PostgreSQL queries

### 1. Connect to PostgreSQL database
```bash
kubectl run temp-psql --image=postgres:15-alpine --rm -it --restart=Never --namespace=namespace-104b --env="PGPASSWORD=postgres123" -- psql -h postgres -U postgres -d userdb
```

### 2. Check for slow queries using pg_stat_statements
```sql
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
WHERE query NOT LIKE '%pg_stat_statements%'
ORDER BY mean_exec_time DESC
LIMIT 10;
```

### 3. For any slow query, run EXPLAIN ANALYZE
```sql
EXPLAIN ANALYZE SELECT * FROM users WHERE email = 'user1@example.com';
```

### 4. Check table structure for missing indexes
```sql
\d users
```

### 5. Look for Sequential Scans in EXPLAIN output
If you see "Seq Scan" on a large table, it indicates a missing index.

### 6. Create index if needed
```sql
CREATE INDEX idx_users_email ON users(email);
```
