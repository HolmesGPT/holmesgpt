"""
Tests for Docker CLI command parsing, validation, and stringification.

This module tests the Docker CLI integration in the bash toolset, ensuring:
1. Safe Docker commands are allowed and properly parsed
2. Unsafe Docker commands are blocked with appropriate error messages
3. Docker command options are validated correctly
4. Commands are properly stringified back to safe command strings
"""

import argparse
import pytest
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestDockerCliSafeCommands:
    """Test Docker CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Container listing and inspection
            ("docker ps", "docker ps"),
            ("docker ps -a", "docker ps -a"),
            ("docker ps --all", "docker ps --all"),
            ("docker ps --quiet", "docker ps --quiet"),
            ("docker ps -q", "docker ps -q"),
            ("docker ps --filter status=running", "docker ps --filter status=running"),
            ("docker ps --format table", "docker ps --format table"),
            ("docker ps --format json", "docker ps --format json"),
            ("docker ps --no-trunc", "docker ps --no-trunc"),
            ("docker ps --size", "docker ps --size"),
            ("docker ps -s", "docker ps -s"),
            ("docker ps --latest", "docker ps --latest"),
            ("docker ps -l", "docker ps -l"),
            # Container management (read-only)
            ("docker container ls", "docker container ls"),
            ("docker container list", "docker container list"),
            ("docker container ls -a", "docker container ls -a"),
            (
                "docker container inspect mycontainer",
                "docker container inspect mycontainer",
            ),
            ("docker container logs mycontainer", "docker container logs mycontainer"),
            (
                "docker container logs mycontainer --tail 100",
                "docker container logs mycontainer --tail 100",
            ),
            (
                "docker container logs mycontainer --follow",
                "docker container logs mycontainer --follow",
            ),
            (
                "docker container logs mycontainer --timestamps",
                "docker container logs mycontainer --timestamps",
            ),
            (
                "docker container logs mycontainer -t",
                "docker container logs mycontainer -t",
            ),
            (
                "docker container logs mycontainer --since 1h",
                "docker container logs mycontainer --since 1h",
            ),
            (
                "docker container logs mycontainer --until 2023-01-01",
                "docker container logs mycontainer --until 2023-01-01",
            ),
            ("docker container stats", "docker container stats"),
            (
                "docker container stats mycontainer",
                "docker container stats mycontainer",
            ),
            (
                "docker container stats --no-stream",
                "docker container stats --no-stream",
            ),
            ("docker container top mycontainer", "docker container top mycontainer"),
            ("docker container port mycontainer", "docker container port mycontainer"),
            (
                "docker container port mycontainer 80",
                "docker container port mycontainer 80",
            ),
            ("docker container diff mycontainer", "docker container diff mycontainer"),
            # Image listing and inspection
            ("docker images", "docker images"),
            ("docker images -a", "docker images -a"),
            ("docker images --all", "docker images --all"),
            ("docker images --quiet", "docker images --quiet"),
            ("docker images -q", "docker images -q"),
            ("docker images --no-trunc", "docker images --no-trunc"),
            (
                "docker images --filter dangling=true",
                "docker images --filter dangling=true",
            ),
            ("docker images --format table", "docker images --format table"),
            ("docker images --format json", "docker images --format json"),
            ("docker image ls", "docker image ls"),
            ("docker image list", "docker image list"),
            ("docker image inspect nginx", "docker image inspect nginx"),
            ("docker image inspect nginx:latest", "docker image inspect nginx:latest"),
            ("docker image history nginx", "docker image history nginx"),
            (
                "docker image history nginx --no-trunc",
                "docker image history nginx --no-trunc",
            ),
            (
                "docker image history nginx --quiet",
                "docker image history nginx --quiet",
            ),
            (
                "docker image history nginx --format table",
                "docker image history nginx --format table",
            ),
            # System information
            ("docker version", "docker version"),
            ("docker version --format json", "docker version --format json"),
            ("docker info", "docker info"),
            ("docker info --format json", "docker info --format json"),
            ("docker system info", "docker system info"),
            ("docker system events", "docker system events"),
            ("docker system events --since 1h", "docker system events --since 1h"),
            (
                "docker system events --until 2023-01-01",
                "docker system events --until 2023-01-01",
            ),
            (
                "docker system events --filter type=container",
                "docker system events --filter type=container",
            ),
            ("docker system df", "docker system df"),
            ("docker system df --verbose", "docker system df --verbose"),
            # Network inspection
            ("docker network ls", "docker network ls"),
            ("docker network list", "docker network list"),
            ("docker network ls --quiet", "docker network ls --quiet"),
            ("docker network ls -q", "docker network ls -q"),
            ("docker network ls --no-trunc", "docker network ls --no-trunc"),
            (
                "docker network ls --filter driver=bridge",
                "docker network ls --filter driver=bridge",
            ),
            ("docker network ls --format table", "docker network ls --format table"),
            ("docker network inspect bridge", "docker network inspect bridge"),
            ("docker network inspect mynetwork", "docker network inspect mynetwork"),
            (
                "docker network inspect mynetwork --format json",
                "docker network inspect mynetwork --format json",
            ),
            # Volume inspection
            ("docker volume ls", "docker volume ls"),
            ("docker volume list", "docker volume list"),
            ("docker volume ls --quiet", "docker volume ls --quiet"),
            ("docker volume ls -q", "docker volume ls -q"),
            (
                "docker volume ls --filter dangling=true",
                "docker volume ls --filter dangling=true",
            ),
            ("docker volume ls --format table", "docker volume ls --format table"),
            ("docker volume inspect myvolume", "docker volume inspect myvolume"),
            (
                "docker volume inspect myvolume --format json",
                "docker volume inspect myvolume --format json",
            ),
            # Registry operations (read-only)
            ("docker search nginx", "docker search nginx"),
            ("docker search nginx --limit 10", "docker search nginx --limit 10"),
            ("docker search nginx --stars 100", "docker search nginx --stars 100"),
            ("docker search nginx --automated", "docker search nginx --automated"),
            ("docker search nginx --official", "docker search nginx --official"),
            ("docker search nginx --no-trunc", "docker search nginx --no-trunc"),
            # Plugin inspection
            ("docker plugin ls", "docker plugin ls"),
            ("docker plugin list", "docker plugin list"),
            ("docker plugin ls --quiet", "docker plugin ls --quiet"),
            ("docker plugin ls -q", "docker plugin ls -q"),
            ("docker plugin ls --no-trunc", "docker plugin ls --no-trunc"),
            ("docker plugin inspect myplugin", "docker plugin inspect myplugin"),
            # Swarm information (read-only)
            ("docker node ls", "docker node ls"),
            ("docker node list", "docker node list"),
            ("docker node ls --quiet", "docker node ls --quiet"),
            ("docker node ls -q", "docker node ls -q"),
            (
                "docker node ls --filter role=manager",
                "docker node ls --filter role=manager",
            ),
            ("docker node ls --format table", "docker node ls --format table"),
            ("docker node inspect mynode", "docker node inspect mynode"),
            ("docker node inspect self", "docker node inspect self"),
            # Service information (read-only)
            ("docker service ls", "docker service ls"),
            ("docker service list", "docker service list"),
            ("docker service ls --quiet", "docker service ls --quiet"),
            ("docker service ls -q", "docker service ls -q"),
            (
                "docker service ls --filter mode=replicated",
                "docker service ls --filter mode=replicated",
            ),
            ("docker service ls --format table", "docker service ls --format table"),
            ("docker service inspect myservice", "docker service inspect myservice"),
            ("docker service logs myservice", "docker service logs myservice"),
            (
                "docker service logs myservice --tail 100",
                "docker service logs myservice --tail 100",
            ),
            (
                "docker service logs myservice --follow",
                "docker service logs myservice --follow",
            ),
            (
                "docker service logs myservice --timestamps",
                "docker service logs myservice --timestamps",
            ),
            ("docker service ps myservice", "docker service ps myservice"),
            (
                "docker service ps myservice --quiet",
                "docker service ps myservice --quiet",
            ),
            (
                "docker service ps myservice --no-trunc",
                "docker service ps myservice --no-trunc",
            ),
            # Stack information (read-only)
            ("docker stack ls", "docker stack ls"),
            ("docker stack list", "docker stack list"),
            ("docker stack ls --format table", "docker stack ls --format table"),
            ("docker stack ps mystack", "docker stack ps mystack"),
            ("docker stack ps mystack --quiet", "docker stack ps mystack --quiet"),
            (
                "docker stack ps mystack --no-trunc",
                "docker stack ps mystack --no-trunc",
            ),
            ("docker stack services mystack", "docker stack services mystack"),
            (
                "docker stack services mystack --quiet",
                "docker stack services mystack --quiet",
            ),
            (
                "docker stack services mystack --format table",
                "docker stack services mystack --format table",
            ),
            # Secret inspection (read-only metadata)
            ("docker secret ls", "docker secret ls"),
            ("docker secret list", "docker secret list"),
            ("docker secret ls --quiet", "docker secret ls --quiet"),
            ("docker secret ls -q", "docker secret ls -q"),
            ("docker secret ls --format table", "docker secret ls --format table"),
            ("docker secret inspect mysecret", "docker secret inspect mysecret"),
            # Config inspection (read-only)
            ("docker config ls", "docker config ls"),
            ("docker config list", "docker config list"),
            ("docker config ls --quiet", "docker config ls --quiet"),
            ("docker config ls -q", "docker config ls -q"),
            ("docker config ls --format table", "docker config ls --format table"),
            ("docker config inspect myconfig", "docker config inspect myconfig"),
            # Context information
            ("docker context ls", "docker context ls"),
            ("docker context list", "docker context list"),
            ("docker context ls --quiet", "docker context ls --quiet"),
            ("docker context ls -q", "docker context ls -q"),
            ("docker context ls --format table", "docker context ls --format table"),
            ("docker context inspect mycontext", "docker context inspect mycontext"),
            ("docker context show", "docker context show"),
            # Commands with complex filters
            (
                "docker ps --filter status=running --filter name=web",
                "docker ps --filter status=running --filter name=web",
            ),
            (
                "docker images --filter dangling=true --filter before=nginx",
                "docker images --filter dangling=true --filter before=nginx",
            ),
            (
                "docker network ls --filter driver=bridge --filter scope=local",
                "docker network ls --filter driver=bridge --filter scope=local",
            ),
            # Commands with multiple formatting options
            (
                "docker ps --format 'table {{.Names}}\\t{{.Status}}'",
                "docker ps --format 'table {{.Names}}\\t{{.Status}}'",
            ),
            (
                "docker images --format 'table {{.Repository}}:{{.Tag}}\\t{{.Size}}'",
                "docker images --format 'table {{.Repository}}:{{.Tag}}\\t{{.Size}}'",
            ),
        ],
    )
    def test_docker_safe_commands(self, input_command: str, expected_output: str):
        """Test that safe Docker commands are parsed and stringified correctly."""
        config = BashExecutorConfig()
        output_command = make_command_safe(input_command, config=config)
        assert output_command == expected_output


class TestDockerCliUnsafeCommands:
    """Test Docker CLI unsafe commands that should be rejected."""

    @pytest.mark.parametrize(
        "command,expected_exception,partial_error_message_content",
        [
            # Container lifecycle operations
            ("docker run nginx", ValueError, "Command is blocked"),
            ("docker create nginx", ValueError, "Command is blocked"),
            ("docker start mycontainer", ValueError, "Command is blocked"),
            ("docker stop mycontainer", ValueError, "Command is blocked"),
            ("docker restart mycontainer", ValueError, "Command is blocked"),
            ("docker pause mycontainer", ValueError, "Command is blocked"),
            ("docker unpause mycontainer", ValueError, "Command is blocked"),
            ("docker kill mycontainer", ValueError, "Command is blocked"),
            ("docker remove mycontainer", ValueError, "Command is blocked"),
            ("docker rm mycontainer", ValueError, "Command is blocked"),
            ("docker exec mycontainer ls", ValueError, "Command is blocked"),
            ("docker attach mycontainer", ValueError, "Command is blocked"),
            ("docker cp mycontainer:/file .", ValueError, "Command is blocked"),
            ("docker commit mycontainer", ValueError, "Command is blocked"),
            ("docker update mycontainer", ValueError, "Command is blocked"),
            # Container export operations (filesystem exfiltration risk)
            ("docker container export mycontainer", ValueError, "Command is blocked"),
            (
                "docker container export mycontainer --output container.tar",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker rename mycontainer newname",
                ValueError,
                "Command is blocked",
            ),
            ("docker wait mycontainer", ValueError, "Command is blocked"),
            # Image operations
            ("docker build .", ValueError, "Command is blocked"),
            ("docker pull nginx", ValueError, "Command is blocked"),
            ("docker push myimage", ValueError, "Command is blocked"),
            ("docker tag nginx myimage", ValueError, "Command is blocked"),
            ("docker untag myimage", ValueError, "Command is blocked"),
            ("docker rmi nginx", ValueError, "Command is blocked"),
            ("docker load < image.tar", ValueError, "Command is blocked"),
            ("docker import image.tar", ValueError, "Command is blocked"),
            ("docker save nginx", ValueError, "Command is blocked"),
            # Network operations
            (
                "docker network create mynetwork",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker network rm mynetwork",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker network remove mynetwork",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker network connect mynetwork mycontainer",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker network disconnect mynetwork mycontainer",
                ValueError,
                "Command is blocked",
            ),
            ("docker network prune", ValueError, "Command is blocked"),
            # Volume operations
            (
                "docker volume create myvolume",
                ValueError,
                "Command is blocked",
            ),
            ("docker volume rm myvolume", ValueError, "Command is blocked"),
            (
                "docker volume remove myvolume",
                ValueError,
                "Command is blocked",
            ),
            ("docker volume prune", ValueError, "Command is blocked"),
            # System operations
            ("docker system prune", ValueError, "Command is blocked"),
            (
                "docker container prune",
                ValueError,
                "Command is blocked",
            ),
            ("docker image prune", ValueError, "Command is blocked"),
            # Registry operations
            ("docker login", ValueError, "Command is blocked"),
            ("docker logout", ValueError, "Command is blocked"),
            # Plugin operations
            (
                "docker plugin install myplugin",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker plugin enable myplugin",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker plugin disable myplugin",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker plugin upgrade myplugin",
                ValueError,
                "Command is blocked",
            ),
            ("docker plugin rm myplugin", ValueError, "Command is blocked"),
            (
                "docker plugin remove myplugin",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker plugin push myplugin",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker plugin create myplugin",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker plugin set myplugin",
                ValueError,
                "Command is blocked",
            ),
            # Swarm operations
            ("docker swarm init", ValueError, "Command is blocked"),
            ("docker swarm join", ValueError, "Command is blocked"),
            ("docker swarm leave", ValueError, "Command is blocked"),
            ("docker swarm update", ValueError, "Command is blocked"),
            (
                "docker swarm join-token",
                ValueError,
                "Command is blocked",
            ),
            ("docker swarm unlock", ValueError, "Command is blocked"),
            (
                "docker swarm unlock-key",
                ValueError,
                "Command is blocked",
            ),
            # Node operations
            (
                "docker node update mynode",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker node demote mynode",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker node promote mynode",
                ValueError,
                "Command is blocked",
            ),
            ("docker node rm mynode", ValueError, "Command is blocked"),
            (
                "docker node remove mynode",
                ValueError,
                "Command is blocked",
            ),
            # Service operations
            (
                "docker service create myservice",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker service update myservice",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker service scale myservice=3",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker service rm myservice",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker service remove myservice",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker service rollback myservice",
                ValueError,
                "Command is blocked",
            ),
            # Stack operations
            (
                "docker stack deploy mystack",
                ValueError,
                "Command is blocked",
            ),
            ("docker stack rm mystack", ValueError, "Command is blocked"),
            (
                "docker stack remove mystack",
                ValueError,
                "Command is blocked",
            ),
            # Secret operations
            (
                "docker secret create mysecret",
                ValueError,
                "Command is blocked",
            ),
            ("docker secret rm mysecret", ValueError, "Command is blocked"),
            (
                "docker secret remove mysecret",
                ValueError,
                "Command is blocked",
            ),
            # Config operations
            (
                "docker config create myconfig",
                ValueError,
                "Command is blocked",
            ),
            ("docker config rm myconfig", ValueError, "Command is blocked"),
            (
                "docker config remove myconfig",
                ValueError,
                "Command is blocked",
            ),
            # Context operations
            (
                "docker context create mycontext",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker context rm mycontext",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker context remove mycontext",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker context update mycontext",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker context use mycontext",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker context export mycontext",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker context import mycontext",
                ValueError,
                "Command is blocked",
            ),
            # Buildx operations
            ("docker buildx build .", ValueError, "Command is blocked"),
            ("docker buildx create", ValueError, "Command is blocked"),
            # Compose operations
            ("docker compose up", ValueError, "Command is blocked"),
            ("docker compose down", ValueError, "Command is blocked"),
            # Trust operations
            ("docker trust sign myimage", ValueError, "Command is blocked"),
            ("docker trust revoke myimage", ValueError, "Command is blocked"),
            # Manifest operations
            (
                "docker manifest create mymanifest",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker manifest push mymanifest",
                ValueError,
                "Command is blocked",
            ),
            # Checkpoint operations
            (
                "docker checkpoint create mycontainer",
                ValueError,
                "Command is blocked",
            ),
            (
                "docker checkpoint restore mycontainer",
                ValueError,
                "Command is blocked",
            ),
            # Invalid command
            ("docker nonexistent", ValueError, "not in the allowlist"),
            # Invalid subcommand
            ("docker container nonexistent", ValueError, "not in the allowlist"),
            ("docker network nonexistent", ValueError, "not in the allowlist"),
            ("docker volume nonexistent", ValueError, "not in the allowlist"),
        ],
    )
    def test_docker_unsafe_commands(
        self, command: str, expected_exception: type, partial_error_message_content: str
    ):
        """Test that unsafe Docker commands are properly rejected."""
        config = BashExecutorConfig()
        with pytest.raises(expected_exception) as exc_info:
            make_command_safe(command, config=config)

        if partial_error_message_content:
            assert partial_error_message_content in str(exc_info.value)


class TestDockerCliEdgeCases:
    """Test edge cases and error conditions for Docker CLI parsing."""

    def test_docker_with_grep_combination(self):
        """Test Docker commands combined with grep."""
        config = BashExecutorConfig()

        # Valid combination
        result = make_command_safe("docker ps | grep nginx", config=config)
        assert result == "docker ps | grep nginx"

        # Invalid - unsafe Docker command with grep
        with pytest.raises(ValueError):
            make_command_safe("docker run nginx | grep success", config=config)

    def test_docker_empty_command(self):
        """Test Docker commands with missing command."""
        config = BashExecutorConfig()

        # Missing command should fail at argument parsing level
        with pytest.raises((argparse.ArgumentError, ValueError)):
            make_command_safe("docker", config=config)

    def test_docker_help_commands(self):
        """Test Docker help commands are allowed."""
        config = BashExecutorConfig()

        # Individual command help works with specific commands
        result = make_command_safe("docker ps", config=config)
        assert result == "docker ps"

    def test_docker_complex_valid_parameters(self):
        """Test Docker commands with complex but valid parameters."""
        config = BashExecutorConfig()

        # Complex container listing with multiple filters
        complex_cmd = "docker ps --all --filter status=running --filter name=web --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}' --no-trunc"
        result = make_command_safe(complex_cmd, config=config)
        assert "docker ps" in result
        assert "--all" in result
        assert "--filter status=running" in result
        assert "--filter name=web" in result
        assert "--format" in result
        assert "--no-trunc" in result

    def test_docker_image_name_validation(self):
        """Test Docker image name validation."""
        config = BashExecutorConfig()

        # Valid image names
        valid_images = [
            "nginx",
            "nginx:latest",
            "registry.io/nginx:v1.0",
            "localhost:5000/myapp",
        ]

        for image in valid_images:
            result = make_command_safe(f"docker image inspect {image}", config=config)
            assert f"docker image inspect {image}" == result

    def test_docker_container_name_validation(self):
        """Test Docker container name validation."""
        config = BashExecutorConfig()

        # Valid container names
        valid_names = [
            "mycontainer",
            "my-container",
            "my_container",
            "container123",
            "web-server-1",
        ]

        for name in valid_names:
            result = make_command_safe(
                f"docker container inspect {name}", config=config
            )
            assert f"docker container inspect {name}" == result

    def test_docker_case_sensitivity(self):
        """Test that Docker commands are case-sensitive where appropriate."""
        config = BashExecutorConfig()

        # Commands should be lowercase
        with pytest.raises(ValueError):
            make_command_safe("docker PS", config=config)

        # Container names are case-sensitive (should be allowed)
        result = make_command_safe(
            "docker container inspect MyContainer", config=config
        )
        assert result == "docker container inspect MyContainer"
