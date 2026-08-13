# MariaDB (MCP)

!!! warning "This integration has been removed"

    The MariaDB MCP server has been removed. Use the built-in [MariaDB](database-mariadb.md)
    data source instead — it connects directly to MariaDB and covers the same
    troubleshooting queries without a separate server pod.

    To migrate, remove `mcpAddons.mariadb` from your Helm values and configure the
    built-in data source with your database connection URL. The chart no longer renders
    the MCP addon, so leaving the old value in place leaves you with no MariaDB access.

See **[MariaDB](database-mariadb.md)** for setup instructions, including how to create a
read-only database user.
