import base64
import json
import os
import sys

import requests

# --- Configuration ---
RABBITMQ_USER = "user"
RABBITMQ_PASS = os.environ.get("RABBITMQ_PASS")
RABBITMQ_HOST = "localhost"
RABBITMQ_PORT = os.environ.get("RABBITMQ_PORT", "15672")
VHOST = "my_vhost3"  # Using a custom vhost
EXCHANGE_NAME = "my_exchange3"
QUEUE_NAME = "my_queue3"
ROUTING_KEY = "my_routing_key3"
MESSAGE = "Test message via python requests"

# --- Setup ---
base_url = f"http://{RABBITMQ_HOST}:{RABBITMQ_PORT}/api"
auth = (RABBITMQ_USER, RABBITMQ_PASS)
headers = {"content-type": "application/json"}


# --- Helper function to check response ---
def check_response(response, step_name):
    """Checks the HTTP response status and prints messages."""
    print(f"--- {step_name} ---")
    print(f"URL: {response.request.url}")
    print(f"Method: {response.request.method}")
    if response.request.body:
        try:
            # Attempt to pretty-print JSON body
            body_json = json.loads(response.request.body)
            print(f"Request Body:\n{json.dumps(body_json, indent=2)}")
        except json.JSONDecodeError:
            # Fallback for non-JSON or already encoded body
            print(f"Request Body: {response.request.body}")  # Might be bytes

    print(f"Status Code: {response.status_code}")
    if response.text:
        try:
            # Attempt to pretty-print JSON response
            response_json = response.json()
            print(f"Response Body:\n{json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body:\n{response.text}")  # Print raw text if not JSON
    else:
        print("Response Body: (empty)")

    print("-" * (len(step_name) + 8))  # Print separator

    if response.status_code >= 200 and response.status_code < 300:
        print(f"SUCCESS: {step_name} completed.")
        return True
    else:
        print(f"ERROR: {step_name} failed!", file=sys.stderr)
        return False


# --- Main Script Logic ---
try:
    # 1. Create the Virtual Host
    vhost_url = f"{base_url}/vhosts/{VHOST}"
    response = requests.put(vhost_url, auth=auth)
    if (
        not check_response(response, "1. Create VHost") and response.status_code != 409
    ):  # 409 Conflict is ok if it exists
        # Allow script to continue if vhost already exists (common scenario)
        if response.status_code == 400 and "already exists" in response.text:
            print("INFO: VHost already exists, continuing...")
        else:
            sys.exit(1)  # Exit on other errors
    print("\n")

    # 2. Create the Exchange
    exchange_url = f"{base_url}/exchanges/{VHOST}/{EXCHANGE_NAME}"
    exchange_payload = {
        "type": "direct",
        "durable": True,
        "auto_delete": False,
        "internal": False,
        "arguments": {},
    }
    response = requests.put(
        exchange_url, auth=auth, headers=headers, json=exchange_payload
    )
    if (
        not check_response(response, "2. Create Exchange")
        and response.status_code != 409
    ):  # 409 is ok if exists
        # Allow script to continue if exchange already exists
        if response.status_code == 400 and "already exists" in response.text:
            print("INFO: Exchange already exists, continuing...")
        elif (
            response.status_code == 201 or response.status_code == 204
        ):  # 201 Created or 204 No Content (means exists)
            print("INFO: Exchange created or already exists.")
        else:
            sys.exit(1)  # Exit on other errors
    print("\n")

    # 3. Create the Queue
    queue_url = f"{base_url}/queues/{VHOST}/{QUEUE_NAME}"
    queue_payload = {"durable": True, "auto_delete": False, "arguments": {}}
    response = requests.put(queue_url, auth=auth, headers=headers, json=queue_payload)
    if (
        not check_response(response, "3. Create Queue") and response.status_code != 409
    ):  # 409 is ok if exists
        # Allow script to continue if queue already exists
        if response.status_code == 400 and "already exists" in response.text:
            print("INFO: Queue already exists, continuing...")
        elif (
            response.status_code == 201 or response.status_code == 204
        ):  # 201 Created or 204 No Content (means exists)
            print("INFO: Queue created or already exists.")
        else:
            sys.exit(1)  # Exit on other errors
    print("\n")

    # 4. Bind the Queue to the Exchange
    binding_url = f"{base_url}/bindings/{VHOST}/e/{EXCHANGE_NAME}/q/{QUEUE_NAME}"
    binding_payload = {"routing_key": ROUTING_KEY, "arguments": {}}
    response = requests.post(
        binding_url, auth=auth, headers=headers, json=binding_payload
    )
    # Note: A successful binding often returns 201 Created. Re-binding might return different codes or errors depending on server config.
    # We'll treat 201 as the main success.
    if not check_response(response, "4. Bind Queue") and response.status_code != 201:
        # Check if binding already exists (often indicated by a specific error message or possibly a 200 OK depending on version/config)
        # This part is less standardized across RabbitMQ versions/configs for re-binding via API
        if (
            response.status_code == 400 and "already exists" in response.text
        ):  # Check for common error message
            print("INFO: Binding already exists, continuing...")
        elif (
            response.status_code == 200
        ):  # Some setups might return 200 OK on re-binding attempt
            print("INFO: Binding might already exist (got 200 OK), continuing...")
        elif response.status_code == 204:  # Or 204 No Content if binding exists
            print("INFO: Binding already exists (got 204 No Content), continuing...")
        # else:
        #     sys.exit(1) # Exit on unexpected errors
    print("\n")

    # 5. Publish a Message
    publish_url = f"{base_url}/exchanges/{VHOST}/{EXCHANGE_NAME}/publish"
    # Encode the message payload to base64
    payload_base64 = base64.b64encode(MESSAGE.encode("utf-8")).decode("utf-8")
    publish_payload = {
        "properties": {},
        "routing_key": ROUTING_KEY,
        "payload": payload_base64,
        "payload_encoding": "base64",
    }
    response = requests.post(
        publish_url, auth=auth, headers=headers, json=publish_payload
    )
    if not check_response(response, "5. Publish Message"):
        sys.exit(1)
    print("\n")

    # # 6. Get (Consume) the Message
    # get_url = f"{base_url}/queues/{VHOST}/{QUEUE_NAME}/get"
    # get_payload = {
    #     "count": 1,
    #     "ackmode": "ack_requeue_false", # Acknowledge and remove
    #     "encoding": "auto",            # Try to decode as UTF-8
    #     "truncate": 50000
    # }
    # response = requests.post(get_url, auth=auth, headers=headers, json=get_payload)
    # if not check_response(response, "6. Get Message"):
    #      # If the queue is empty (status 200 OK, but empty array), it's not an error here
    #      if response.status_code == 200 and response.json() == []:
    #          print("INFO: Queue is empty, no message retrieved.")
    #      else:
    #          sys.exit(1)
    # print("\n")

    print("Script finished successfully.")

except requests.exceptions.ConnectionError as e:
    print(
        f"\nERROR: Could not connect to RabbitMQ Management API at {base_url}",
        file=sys.stderr,
    )
    print(
        "Please ensure RabbitMQ is running and the Management Plugin is enabled.",
        file=sys.stderr,
    )
    print(f"Details: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
    sys.exit(1)
