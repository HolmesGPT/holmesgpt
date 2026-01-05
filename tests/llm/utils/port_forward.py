import os
import signal
import subprocess
import time
from typing import Any, Dict, List, Optional

from tests.llm.utils.setup_cleanup import log
from tests.llm.utils.test_case_utils import HolmesTestCase


class PortForward:
    """Manages a single port forward process."""

    def __init__(self, namespace: str, service: str, local_port: int, remote_port: int):
        self.namespace = namespace
        self.service = service
        self.local_port = local_port
        self.remote_port = remote_port
        self.process: Optional[subprocess.Popen] = None
        self.command = f"kubectl port-forward -n {namespace} svc/{service} {local_port}:{remote_port}"

    def start(self) -> None:
        """Start the port forward process."""
        try:
            log(
                f"🔌 Starting port forward: {self.service}:{self.remote_port} -> localhost:{self.local_port}"
            )

            # Check if port is already in use
            self._check_port_availability()

            # Start the process - use DEVNULL to avoid pipe buffer issues
            self.process = subprocess.Popen(
                self.command.split(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                universal_newlines=True,
                preexec_fn=os.setsid if os.name != "nt" else None,
            )

            # Give kubectl time to establish the port forward
            time.sleep(3)

            # Check if process is still running
            if self.process.poll() is not None:
                # Process exited - likely due to port conflict or other error
                # Since we're using DEVNULL, we can't get the exact error message
                # Check if port is in use to provide better error message
                if _is_port_in_use(self.local_port):
                    self._report_port_conflict()
                    raise RuntimeError(
                        f"Port forward failed: Port {self.local_port} is already in use"
                    )
                else:
                    raise RuntimeError(
                        f"Port forward failed to start for {self.service}"
                    )

            log(
                f"✅ Port forward established: {self.service}:{self.remote_port} -> localhost:{self.local_port}"
            )
        except Exception as e:
            log(f"❌ Failed to start port forward: {e}")
            raise

    def _check_port_availability(self) -> None:
        """Check if the port is already in use."""
        if _is_port_in_use(self.local_port):
            self._report_port_conflict()
            raise RuntimeError(
                f"Port {self.local_port} is already in use. Please kill the existing process or use a different port."
            )

    def _report_port_conflict(self) -> None:
        """Report detailed information about what's using the port."""
        try:
            if os.name != "nt":
                # Get detailed info about the process using the port
                result = subprocess.run(
                    ["lsof", "-i", f":{self.local_port}"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    log(f"\n📍 Port {self.local_port} is being used by:")
                    log(result.stdout)
                    log("\nTo fix this, either:")
                    log(
                        f"1. Kill the specific process using the port: kill $(lsof -ti :{self.local_port})"
                    )
                    log(
                        '2. Kill all kubectl port-forward processes: pkill -f "kubectl.*port-forward"'
                    )
        except (subprocess.CalledProcessError, OSError) as e:
            # Log specific error but don't fail - this is just informational
            log(f"⚠️ Could not get port info: {e}")

    def stop(self) -> None:
        """Stop the port forward process."""
        if self.process and self.process.poll() is None:
            try:
                if os.name != "nt":
                    # Kill the entire process group
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                else:
                    self.process.terminate()

                # Wait for process to terminate
                self.process.wait(timeout=5)
                log(f"🛑 Port forward stopped: {self.service}")
            except Exception as e:
                log(f"⚠️ Error stopping port forward: {e}")
                # Force kill if graceful termination fails
                try:
                    if os.name != "nt":
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    else:
                        self.process.kill()
                except Exception:
                    pass

    def __eq__(self, other):
        """Compare port forwards by their configuration."""
        if not isinstance(other, PortForward):
            return False
        return (
            self.namespace == other.namespace
            and self.service == other.service
            and self.local_port == other.local_port
            and self.remote_port == other.remote_port
        )

    def __hash__(self):
        """Hash based on configuration."""
        return hash((self.namespace, self.service, self.local_port, self.remote_port))


class PortForwardManager:
    """Manages multiple port forwards across all tests."""

    def __init__(self):
        self.port_forwards: List[PortForward] = []

    def add_port_forward(self, config: Dict[str, Any]) -> None:
        """Add a port forward configuration."""
        pf = PortForward(
            namespace=config["namespace"],
            service=config["service"],
            local_port=config["local_port"],
            remote_port=config["remote_port"],
        )
        # Only add if not already present
        if pf not in self.port_forwards:
            self.port_forwards.append(pf)

    def start_all(self) -> None:
        """Start all port forwards."""
        for pf in self.port_forwards:
            pf.start()

    def stop_all(self) -> None:
        """Stop all port forwards."""
        for pf in self.port_forwards:
            pf.stop()


def extract_port_forwards_from_test_cases(
    test_cases: List[HolmesTestCase],
) -> List[Dict[str, Any]]:
    """Extract unique port forward configurations from all test cases."""
    seen = set()
    unique_configs = []

    for test_case in test_cases:
        if test_case.port_forwards:
            for config in test_case.port_forwards:
                # Create a hashable key for deduplication
                key = (
                    config["namespace"],
                    config["service"],
                    config["local_port"],
                    config["remote_port"],
                )
                if key not in seen:
                    seen.add(key)
                    unique_configs.append(config)

    return unique_configs


def check_port_availability_early(test_cases: List[HolmesTestCase]) -> Dict[str, str]:
    """Check for port conflicts and availability before running any setup scripts.

    Returns:
        Dict mapping test IDs to skip reasons (e.g., "Port 3200 conflict with test 115_checkout")
    """
    # Track port usage by service to detect real conflicts
    # Key: local_port, Value: Dict[service_key -> List[test_ids]]
    port_service_usage: Dict[int, Dict[str, List[str]]] = {}
    tests_to_skip = {}  # Changed from set to dict to store reasons

    # Collect all port usages
    for test_case in test_cases:
        if test_case.port_forwards:
            for config in test_case.port_forwards:
                local_port = config["local_port"]
                # Create a unique key for the service
                service_key = (
                    f"{config['namespace']}/{config['service']}:{config['remote_port']}"
                )
                test_id = test_case.id

                if local_port not in port_service_usage:
                    port_service_usage[local_port] = {}

                if service_key not in port_service_usage[local_port]:
                    port_service_usage[local_port][service_key] = []

                port_service_usage[local_port][service_key].append(test_id)

    # Check for REAL conflicts - same port used for DIFFERENT services
    conflicts = []
    for port, services in port_service_usage.items():
        if len(services) > 1:
            # Multiple different services want the same port - this is a conflict
            conflict_details = []
            all_conflicting_tests = set()
            for service_key, test_ids in services.items():
                conflict_details.append(f"{service_key} (tests: {', '.join(test_ids)})")
                all_conflicting_tests.update(test_ids)
            conflicts.append((port, conflict_details))

            # Add all conflicting tests to skip list with detailed reason
            for test_id in all_conflicting_tests:
                tests_to_skip[test_id] = (
                    f"Port {port} conflict: multiple services trying to use same port"
                )

    if conflicts:
        error_msg = "\n🚨 Port conflicts detected between tests! Multiple DIFFERENT services want the same local port:\n"
        for port, details in conflicts:
            error_msg += f"\n  ❌ Port {port} CONFLICT - different services need the same port:\n"
            for detail in details:
                error_msg += f"      {detail}\n"

        error_msg += "\n📝 To fix this conflict between tests:"
        error_msg += "\n  1. Update the test_case.yaml files to use different local_port values for different services"
        error_msg += "\n  2. Tests CAN share the same port if they're forwarding to the SAME service"
        error_msg += "\n  3. Suggested fix: Use unique ports based on test number (e.g., test 114 → port 3114, test 115 → port 3115)"
        error_msg += "\n\nAlternatively, you can skip all tests requiring port forwards by running:"
        error_msg += "\n  pytest -m 'not port-forward'"

        log(error_msg)
        log(
            f"⚠️  Will skip {len(tests_to_skip)} tests due to conflicts between tests: {', '.join(sorted(tests_to_skip.keys()))}"
        )
        # Don't raise - we'll skip these tests instead

    # Check if ports are already in use on the system
    ports_in_use = []
    for port in port_service_usage.keys():
        if _is_port_in_use(port):
            ports_in_use.append(port)

    if ports_in_use:
        error_msg = "\n🚨 Ports already in use by external processes on the system:\n"
        for port in ports_in_use:
            # Collect all test IDs that need this port
            test_ids = []
            service_details = []
            for service_key, service_tests in port_service_usage[port].items():
                test_ids.extend(service_tests)
                service_details.append(f"{service_key}")
            unique_test_ids = list(set(test_ids))

            # Show which tests are affected
            error_msg += f"\n  Port {port} is ALREADY IN USE ON SYSTEM"
            error_msg += f"\n    → Service(s): {', '.join(service_details)}"
            error_msg += f"\n    → Tests that need this port: {', '.join(sorted(unique_test_ids))}"
            error_msg += "\n    → These tests will be SKIPPED"

            # Add these tests to skip list with detailed reason
            for test_id in unique_test_ids:
                tests_to_skip[test_id] = (
                    f"Port {port} already in use on system (not by tests)"
                )

        error_msg += "\n\nTo see what external process is using these ports:"
        error_msg += f"\n  lsof -i :{','.join(str(p) for p in ports_in_use)}"
        error_msg += "\n\nTo fix this:"
        error_msg += "\n  1. Kill the external processes using these ports (likely leftover kubectl port-forward)"
        error_msg += "\n  2. Or skip port-forward tests: pytest -m 'not port-forward'"

        log(error_msg)
        log(
            f"⚠️  Will skip {len(tests_to_skip)} tests due to ports already in use by external processes: {', '.join(sorted(tests_to_skip.keys()))}"
        )
        # Don't raise - we'll skip these tests instead

    return tests_to_skip


def _is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Try to bind to the port
        sock.bind(("localhost", port))
        return False  # Port is available
    except OSError:
        return True  # Port is in use
    finally:
        sock.close()


def setup_all_port_forwards(test_cases: List[HolmesTestCase]) -> PortForwardManager:
    """Set up port forwards for all test cases that need them."""
    manager = PortForwardManager()

    # Extract all unique port forward configs
    all_configs = extract_port_forwards_from_test_cases(test_cases)

    if all_configs:
        log(f"\n🔌 Setting up {len(all_configs)} port forwards")
        for config in all_configs:
            manager.add_port_forward(config)

        # Start all port forwards
        try:
            manager.start_all()
        except RuntimeError as e:
            log(f"🔍 DEBUG: Port forward setup failed but continuing: {e}")
            # Continue anyway - tests will still run but may fail if they need the port forward

    return manager


def cleanup_port_forwards_by_config(configs: List[Dict[str, Any]]) -> None:
    """Clean up port forwards by killing kubectl processes matching the configs.

    This approach is used instead of cleanup_all_port_forwards because with xdist
    the worker that created the port forwards may not be the one cleaning them up.
    (We can't use the same worker to clean up because other tests might still be running -
    we need to cleanup on the last worker only.)

    This is more "violent" than using the PortForwardManager's stop() method, but it's
    unfortunately necessary. An alternative is to do setup/cleanu on master worker,
    but I'm not sure how to do that with xdist.
    """
    if not configs:
        return

    log(f"\n🔌 Cleaning up {len(configs)} port forwards")

    for config in configs:
        try:
            # Find and kill kubectl port-forward processes for this specific port
            local_port = config["local_port"]

            if os.name != "nt":
                # On Unix-like systems, find the process using the port
                result = subprocess.run(
                    ["lsof", "-ti", f":{local_port}"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    pids = result.stdout.strip().split("\n")
                    for pid in pids:
                        try:
                            # Check if it's a kubectl process before killing
                            ps_result = subprocess.run(
                                ["ps", "-p", pid, "-o", "comm="],
                                capture_output=True,
                                text=True,
                            )
                            if (
                                ps_result.returncode == 0
                                and "kubectl" in ps_result.stdout
                            ):
                                os.kill(int(pid), signal.SIGTERM)
                                log(
                                    f"🛑 Killed kubectl port-forward on port {local_port} (PID: {pid})"
                                )
                        except (ValueError, ProcessLookupError):
                            pass
        except Exception as e:
            log(f"⚠️ Error cleaning up port forward on port {config['local_port']}: {e}")
