#!/usr/bin/env python3

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from tests.conftest import create_mock_tool_invoke_context

# Add the project root to sys.path so we can import modules
sys.path.insert(0, "/home/nherment/workspace/robusta-dev/holmesgpt")

from holmes.plugins.toolsets.azure_sql.azure_sql_toolset import (
    AzureSQLConfig,
    AzureSQLDatabaseConfig,
    AzureSQLToolset,
    GetActiveAlerts,
)


# Monkey patch the alert monitoring API to return sample alert data
class MockAlertMonitoringAPI:
    """Mock API client that returns sample alert data for demonstration."""

    def __init__(self, credential, subscription_id):
        self.credential = credential
        self.subscription_id = subscription_id

    def get_active_alerts(self, resource_group, server_name, database_name):
        """Return mock active alerts for demonstration."""

        database_resource_id = (
            f"/subscriptions/{self.subscription_id}/"
            f"resourceGroups/{resource_group}/"
            f"providers/Microsoft.Sql/servers/{server_name}/"
            f"databases/{database_name}"
        )

        server_resource_id = (
            f"/subscriptions/{self.subscription_id}/"
            f"resourceGroups/{resource_group}/"
            f"providers/Microsoft.Sql/servers/{server_name}"
        )

        # Create sample alert data that would typically be returned
        sample_alerts = [
            {
                "id": f"{database_resource_id}/providers/Microsoft.Insights/metricAlerts/high-cpu-alert",
                "name": "High CPU Usage Alert",
                "description": "Database CPU usage exceeded 85% for more than 5 minutes",
                "severity": "Sev1",
                "state": "Fired",
                "monitor_condition": "Fired",
                "fired_time": (
                    datetime.now(timezone.utc) - timedelta(minutes=15)
                ).isoformat(),
                "resource_type": "Microsoft.Sql/servers/databases",
                "target_resource": database_resource_id,
                "scope": "database",
            },
            {
                "id": f"{database_resource_id}/providers/Microsoft.Insights/metricAlerts/storage-full-alert",
                "name": "Database Storage Near Capacity",
                "description": "Database storage utilization exceeded 90% of allocated space",
                "severity": "Sev0",
                "state": "Fired",
                "monitor_condition": "Fired",
                "fired_time": (
                    datetime.now(timezone.utc) - timedelta(minutes=8)
                ).isoformat(),
                "resource_type": "Microsoft.Sql/servers/databases",
                "target_resource": database_resource_id,
                "scope": "database",
            },
            {
                "id": f"{server_resource_id}/providers/Microsoft.Insights/metricAlerts/connection-limit-alert",
                "name": "Connection Pool Exhaustion",
                "description": "Server connection pool utilization exceeded 95%",
                "severity": "Sev1",
                "state": "Fired",
                "monitor_condition": "Fired",
                "fired_time": (
                    datetime.now(timezone.utc) - timedelta(minutes=3)
                ).isoformat(),
                "resource_type": "Microsoft.Sql/servers",
                "target_resource": server_resource_id,
                "scope": "server",
            },
            {
                "id": f"{database_resource_id}/providers/Microsoft.Insights/metricAlerts/slow-query-alert",
                "name": "Slow Query Performance",
                "description": "Average query response time exceeded 2 seconds",
                "severity": "Sev2",
                "state": "Fired",
                "monitor_condition": "Fired",
                "fired_time": (
                    datetime.now(timezone.utc) - timedelta(minutes=12)
                ).isoformat(),
                "resource_type": "Microsoft.Sql/servers/databases",
                "target_resource": database_resource_id,
                "scope": "database",
            },
            {
                "id": f"{database_resource_id}/providers/Microsoft.Insights/metricAlerts/dtu-exhaustion-alert",
                "name": "DTU Limit Reached",
                "description": "Database DTU consumption reached 100% of allocated DTUs",
                "severity": "Sev0",
                "state": "Fired",
                "monitor_condition": "Fired",
                "fired_time": (
                    datetime.now(timezone.utc) - timedelta(minutes=20)
                ).isoformat(),
                "resource_type": "Microsoft.Sql/servers/databases",
                "target_resource": database_resource_id,
                "scope": "database",
            },
        ]

        return {
            "database_resource_id": database_resource_id,
            "server_resource_id": server_resource_id,
            "active_alerts": sample_alerts,
            "total_count": len(sample_alerts),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "method": "mock_demo_data",
        }


def patch_alert_monitoring():
    """Monkey patch the alert monitoring API to use mock data."""
    import holmes.plugins.toolsets.azure_sql.azure_sql_toolset as toolset_module

    # Replace the import in the toolset
    toolset_module.AlertMonitoringAPI = MockAlertMonitoringAPI
    print("🎭 Patched AlertMonitoringAPI to use mock demonstration data")


def main():
    # Set up logging
    logging.basicConfig(level=logging.INFO)

    print("🎭 Azure SQL Active Alerts Demo Report")
    print("=" * 50)
    print("This demonstrates what the GetActiveAlerts report looks like")
    print("when there are actual alerts firing in your Azure SQL environment.")
    print("=" * 50)

    # Read configuration from environment variables
    required_env_vars = [
        "AZURE_SQL_SUBSCRIPTION_ID",
        "AZURE_SQL_RESOURCE_GROUP",
        "AZURE_SQL_SERVER",
        "AZURE_SQL_DATABASE",
    ]

    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)

    # Optional authentication variables
    tenant_id = os.getenv("AZURE_SQL_TENANT_ID")
    client_id = os.getenv("AZURE_SQL_CLIENT_ID")
    client_secret = os.getenv("AZURE_SQL_CLIENT_SECRET")

    print("✅ Environment variables found")
    print(f"   Subscription: {os.getenv('AZURE_SQL_SUBSCRIPTION_ID')}")
    print(f"   Resource Group: {os.getenv('AZURE_SQL_RESOURCE_GROUP')}")
    print(f"   Server: {os.getenv('AZURE_SQL_SERVER')}")
    print(f"   Database: {os.getenv('AZURE_SQL_DATABASE')}")

    # Create configuration
    database_config = AzureSQLDatabaseConfig(
        subscription_id=os.getenv("AZURE_SQL_SUBSCRIPTION_ID"),
        resource_group=os.getenv("AZURE_SQL_RESOURCE_GROUP"),
        server_name=os.getenv("AZURE_SQL_SERVER"),
        database_name=os.getenv("AZURE_SQL_DATABASE"),
    )

    config = AzureSQLConfig(
        database=database_config,
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )

    try:
        # Patch the alert monitoring API to use mock data
        patch_alert_monitoring()

        # Initialize the toolset
        print("\n🔧 Initializing Azure SQL Toolset...")
        toolset = AzureSQLToolset()

        # Test prerequisites
        config_dict = config.model_dump()
        success, message = toolset.prerequisites_callable(config_dict)

        if not success:
            print(f"❌ Prerequisites failed: {message}")
            sys.exit(1)

        print("✅ Prerequisites passed")

        # Create and invoke the GetActiveAlerts tool
        print("\n🚨 Retrieving active alerts (using demo data)...")
        alerts_tool = GetActiveAlerts(toolset)
        result = alerts_tool._invoke({}, context=create_mock_tool_invoke_context())

        if result.status.value == "success":
            print("✅ Successfully retrieved alerts!")
            print("\n" + "=" * 80)
            print("DEMO: ACTIVE ALERTS REPORT WITH SAMPLE ALERTS")
            print("=" * 80)
            print(result.data)
            print("\n" + "=" * 80)
            print("🎭 END OF DEMO REPORT")
            print("=" * 80)
            print(
                "\n💡 This shows how the GetActiveAlerts function displays alerts when they exist."
            )
            print("📊 In a real scenario, these alerts would be triggered by:")
            print("   • High CPU usage (>85%)")
            print("   • Storage near capacity (>90%)")
            print("   • Connection pool exhaustion (>95%)")
            print("   • Slow query performance (>2s response time)")
            print("   • DTU limit reached (100% consumption)")
        else:
            print(f"❌ Failed to retrieve alerts: {result.error}")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        logging.exception("Detailed error:")


if __name__ == "__main__":
    main()
