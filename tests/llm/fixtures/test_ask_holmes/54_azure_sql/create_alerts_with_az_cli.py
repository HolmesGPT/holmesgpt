#!/usr/bin/env python3

import os
import subprocess
import sys
import time


def run_az_command(command):
    """Run an Azure CLI command and return the result."""
    try:
        print(f"🔧 Running: {command}")
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print("✅ Success")
            return result.stdout.strip()
        else:
            print(f"❌ Failed: {result.stderr.strip()}")
            return None
    except subprocess.TimeoutExpired:
        print("⏰ Command timed out")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def login_to_azure():
    """Login to Azure using service principal credentials."""
    tenant_id = os.getenv("AZURE_SQL_TENANT_ID")
    client_id = os.getenv("AZURE_SQL_CLIENT_ID")
    client_secret = os.getenv("AZURE_SQL_CLIENT_SECRET")

    if not all([tenant_id, client_id, client_secret]):
        print("❌ Missing Azure credentials")
        return False

    login_cmd = f"az login --service-principal -u {client_id} -p {client_secret} --tenant {tenant_id}"
    result = run_az_command(login_cmd)
    return result is not None


def set_subscription():
    """Set the Azure subscription."""
    subscription_id = os.getenv("AZURE_SQL_SUBSCRIPTION_ID")
    if not subscription_id:
        print("❌ Missing subscription ID")
        return False

    cmd = f"az account set --subscription {subscription_id}"
    result = run_az_command(cmd)
    return result is not None


def create_test_alert_rules():
    """Create test alert rules using Azure CLI with very low thresholds."""

    subscription_id = os.getenv("AZURE_SQL_SUBSCRIPTION_ID")
    resource_group = os.getenv("AZURE_SQL_RESOURCE_GROUP")
    server_name = os.getenv("AZURE_SQL_SERVER")
    database_name = os.getenv("AZURE_SQL_DATABASE")

    # Build resource ID
    database_resource_id = (
        f"/subscriptions/{subscription_id}/"
        f"resourceGroups/{resource_group}/"
        f"providers/Microsoft.Sql/servers/{server_name}/"
        f"databases/{database_name}"
    )

    print("🚨 Creating test alert rules with very low thresholds...")

    alerts_created = []

    # Alert 1: CPU percentage > 1% (will trigger immediately)
    print("\n1️⃣ Creating CPU usage alert (threshold: 1%)")
    cpu_alert_cmd = f"""az monitor metrics alert create \\
        --name "test-cpu-alert-low-threshold" \\
        --resource-group "{resource_group}" \\
        --description "Test alert: CPU usage above 1% - WILL TRIGGER" \\
        --severity 2 \\
        --window-size 5m \\
        --evaluation-frequency 1m \\
        --condition "avg cpu_percent > 1" \\
        --scopes "{database_resource_id}\""""

    result = run_az_command(cpu_alert_cmd)
    if result:
        alerts_created.append("test-cpu-alert-low-threshold")

    # Alert 2: Storage > 1MB (will trigger for any database with data)
    print("\n2️⃣ Creating storage usage alert (threshold: 1MB)")
    storage_alert_cmd = f"""az monitor metrics alert create \\
        --name "test-storage-alert-low-threshold" \\
        --resource-group "{resource_group}" \\
        --description "Test alert: Database size above 1MB - WILL TRIGGER" \\
        --severity 3 \\
        --window-size 15m \\
        --evaluation-frequency 5m \\
        --condition "max storage > 1048576" \\
        --scopes "{database_resource_id}\""""

    result = run_az_command(storage_alert_cmd)
    if result:
        alerts_created.append("test-storage-alert-low-threshold")

    # Alert 3: DTU consumption > 1% (will trigger with any activity)
    print("\n3️⃣ Creating DTU consumption alert (threshold: 1%)")
    dtu_alert_cmd = f"""az monitor metrics alert create \\
        --name "test-dtu-alert-low-threshold" \\
        --resource-group "{resource_group}" \\
        --description "Test alert: DTU consumption above 1% - WILL TRIGGER" \\
        --severity 1 \\
        --window-size 5m \\
        --evaluation-frequency 1m \\
        --condition "avg dtu_consumption_percent > 1" \\
        --scopes "{database_resource_id}\""""

    result = run_az_command(dtu_alert_cmd)
    if result:
        alerts_created.append("test-dtu-alert-low-threshold")

    # Alert 4: Connection count > 0 (will trigger with any connections)
    print("\n4️⃣ Creating connection count alert (threshold: 0)")
    connection_alert_cmd = f"""az monitor metrics alert create \\
        --name "test-connection-alert-low-threshold" \\
        --resource-group "{resource_group}" \\
        --description "Test alert: Active connections above 0 - WILL TRIGGER" \\
        --severity 2 \\
        --window-size 5m \\
        --evaluation-frequency 1m \\
        --condition "total connection_successful > 0" \\
        --scopes "{database_resource_id}\""""

    result = run_az_command(connection_alert_cmd)
    if result:
        alerts_created.append("test-connection-alert-low-threshold")

    return alerts_created


def wait_for_alerts_to_trigger(wait_minutes=10):
    """Wait for alerts to trigger and provide updates."""
    print(f"\n⏳ Waiting {wait_minutes} minutes for alerts to trigger...")
    print(
        "📊 Azure Monitor typically takes 1-5 minutes to evaluate metrics and fire alerts..."
    )

    for minute in range(wait_minutes):
        remaining = wait_minutes - minute
        print(f"   ⏰ {remaining} minutes remaining...")
        time.sleep(60)

    print("✅ Wait period completed - alerts should have triggered by now!")


def list_created_alerts():
    """List the alert rules we created."""
    resource_group = os.getenv("AZURE_SQL_RESOURCE_GROUP")

    print("\n📋 Listing created alert rules:")
    list_cmd = f"az monitor metrics alert list --resource-group {resource_group} --output table"
    run_az_command(list_cmd)


def cleanup_test_alerts():
    """Clean up the test alert rules."""
    resource_group = os.getenv("AZURE_SQL_RESOURCE_GROUP")

    test_alert_names = [
        "test-cpu-alert-low-threshold",
        "test-storage-alert-low-threshold",
        "test-dtu-alert-low-threshold",
        "test-connection-alert-low-threshold",
    ]

    print("\n🧹 Cleaning up test alert rules...")

    for alert_name in test_alert_names:
        print(f"🗑️ Deleting: {alert_name}")
        delete_cmd = f"az monitor metrics alert delete --name {alert_name} --resource-group {resource_group}"
        run_az_command(delete_cmd)


def main():
    print("🚨 Azure CLI Alert Generation Script")
    print("=" * 50)
    print(
        f"Target Database: {os.getenv('AZURE_SQL_SERVER')}/{os.getenv('AZURE_SQL_DATABASE')}"
    )
    print(f"Resource Group: {os.getenv('AZURE_SQL_RESOURCE_GROUP')}")
    print("=" * 50)

    # Check for cleanup flag
    if len(sys.argv) > 1 and sys.argv[1] == "--cleanup":
        print("🧹 Cleanup mode - removing test alerts...")
        if login_to_azure() and set_subscription():
            cleanup_test_alerts()
        return

    try:
        # Login to Azure
        print("\n🔑 Logging into Azure...")
        if not login_to_azure():
            print("❌ Failed to login to Azure")
            return

        # Set subscription
        print("\n📋 Setting subscription...")
        if not set_subscription():
            print("❌ Failed to set subscription")
            return

        # Create test alerts
        created_alerts = create_test_alert_rules()

        if created_alerts:
            print(f"\n✅ Successfully created {len(created_alerts)} alert rules:")
            for alert in created_alerts:
                print(f"   • {alert}")

            # List the alerts
            list_created_alerts()

            # Wait for alerts to trigger
            wait_for_alerts_to_trigger(10)

            print("\n📊 Alert generation process completed!")
            print(
                "🔍 Now run the GetActiveAlerts function to see the triggered alerts:"
            )
            print("   python test_azure_sql_alerts.py")
            print("\n💡 To clean up test alerts later, run:")
            print("   python create_alerts_with_az_cli.py --cleanup")
        else:
            print("\n❌ Failed to create any alert rules")

    except Exception as e:
        print(f"❌ Error during alert generation: {e}")


if __name__ == "__main__":
    main()
