{{/*
Define the LLM instructions for MariaDB MCP
*/}}
{{- define "holmes.mariadbMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.mariadb.llmInstructions -}}
{{ .Values.mcpAddons.mariadb.llmInstructions }}
{{- else -}}
Use this MariaDB MCP server to troubleshoot database issues.

Sometimes, application that are working with the db can have latency, or even halt.
This often because of issues related to the db, like slow queries because of missing indexes or inefficient queries, load on the db, DB locks etc.
Checking the DB for this issue can help with finding the root cause.
When you do find an issue, provide as much information as possible about the issue you found.

When investigating issues, always:
1. Check current connections and running queries first
2. Look for deadlocks or lock waits if transactions are failing
3. Analyze slow query patterns for performance issues
4. Check table structures and indexes for optimization opportunities
5. Review error logs if available

The server provides tools for:
1. **Database Inspection**:
   - List databases and tables
   - View table schemas and indexes
   - Check table statistics and sizes

2. **Query Analysis**:
   - Execute read-only SQL queries for investigation
   - Analyze slow queries from the slow query log
   - Check current running queries with SHOW PROCESSLIST

3. **Performance Troubleshooting**:
   - Identify deadlocks: Use "SHOW ENGINE INNODB STATUS" to see recent deadlocks
   - Find blocking queries: Check information_schema.innodb_locks and innodb_lock_waits
   - Analyze slow queries: Query performance_schema tables
   - Check connection usage: Use SHOW STATUS LIKE 'Threads_connected'

4. **Common Troubleshooting Queries**:
   - For deadlocks:
     ```sql
     SHOW ENGINE INNODB STATUS;
     SELECT * FROM information_schema.innodb_locks;
     SELECT * FROM information_schema.innodb_lock_waits;
     ```

   - For slow queries:
     ```sql
     SELECT * FROM performance_schema.events_statements_summary_by_digest
     ORDER BY sum_timer_wait DESC LIMIT 10;
     ```

   - For connection issues:
     ```sql
     SHOW STATUS LIKE 'Max_used_connections';
     SHOW VARIABLES LIKE 'max_connections';
     SHOW PROCESSLIST;
     ```

   - For table locks:
     ```sql
     SELECT * FROM information_schema.metadata_locks;
     SELECT * FROM performance_schema.table_handles
     WHERE object_type = 'TABLE' AND owner_thread_id IS NOT NULL;
     ```

5. **Important Notes**:
   - The MCP user (mcp_readonly) has read-only access for safety
   - Performance schema is enabled for detailed diagnostics
   - Slow query log is enabled (queries > 2 seconds are logged)
   - The testdb database contains sample tables for testing
{{- end -}}
{{- end -}}
