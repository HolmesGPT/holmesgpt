# Tempo CLI - Grafana Tempo Command Line Interface

A simple command-line utility for interacting with Grafana Tempo API, exposing all public methods of the `GrafanaTempoAPI` class.

## Installation

This CLI uses the existing Holmes GPT dependencies. Make sure you have the project dependencies installed:

```bash
poetry install
```

## Configuration

Configure the CLI using environment variables or command-line options:

### Environment Variables
- `TEMPO_URL`: Base URL for your Tempo instance (required)
- `TEMPO_API_KEY`: API key for authentication (optional)
- `GRAFANA_DATASOURCE_UID`: Grafana datasource UID for proxying requests (optional)

### Command Line Options
All commands support these global options:
- `--url, -u`: Tempo base URL
- `--api-key, -k`: API key for authentication
- `--grafana-uid, -g`: Grafana datasource UID
- `--use-post, -p`: Use POST method instead of GET

## Usage

### Check Tempo Status
```bash
# Using environment variables
export TEMPO_URL=http://localhost:3100
./tempo_cli.py echo

# Using command line options
./tempo_cli.py echo --url http://localhost:3100
```

### Query Trace by ID
```bash
./tempo_cli.py trace <trace-id>

# With time range
./tempo_cli.py trace <trace-id> --start 1234567890 --end 1234567900
```

### Search Traces by Tags
```bash
# Search for traces with specific tags
./tempo_cli.py search-tags 'service.name="my-service" http.status_code=500'

# With duration filters
./tempo_cli.py search-tags 'service.name="my-service"' --min-duration 5s --max-duration 1m
```

### Search Traces by TraceQL Query
```bash
# Using TraceQL syntax
./tempo_cli.py search-query '{ .service.name = "my-service" && duration > 100ms }'

# With limits
./tempo_cli.py search-query '{ .service.name = "my-service" }' --limit 10
```

### List Available Tag Names
```bash
# List all tag names
./tempo_cli.py tag-names

# Filter by scope
./tempo_cli.py tag-names --scope resource

# With TraceQL filter
./tempo_cli.py tag-names --query '{ .service.name = "my-service" }'
```

### Get Tag Values
```bash
# Get all values for a specific tag
./tempo_cli.py tag-values service.name

# With TraceQL filter
./tempo_cli.py tag-values service.name --query '{ .cluster = "production" }'
```

### Query Metrics (Instant)
```bash
# Query instant metric value
./tempo_cli.py metrics-instant '{ .service.name = "my-service" } | rate()'

# With time range
./tempo_cli.py metrics-instant '{ .service.name = "my-service" } | rate()' --since 1h
```

### Query Metrics (Range)
```bash
# Query time series metrics
./tempo_cli.py metrics-range '{ .service.name = "my-service" } | rate()'

# With step interval
./tempo_cli.py metrics-range '{ .service.name = "my-service" } | rate()' --step 5m --since 3h
```

## Examples

### Complete Example with Authentication
```bash
# Set up environment
export TEMPO_URL=https://tempo.example.com
export TEMPO_API_KEY=your-api-key-here

# Check connectivity
./tempo_cli.py echo

# Search for error traces in the last hour
./tempo_cli.py search-query '{ status.code = "error" }' --since 1h --limit 20

# Get trace details
./tempo_cli.py trace 123abc456def789

# Get available services
./tempo_cli.py tag-values service.name
```

### Using with Grafana Proxy
```bash
# When accessing Tempo through Grafana datasource proxy
export TEMPO_URL=https://grafana.example.com
export GRAFANA_DATASOURCE_UID=tempo-datasource-uid
export TEMPO_API_KEY=grafana-api-key

./tempo_cli.py search-tags 'service.name="frontend"'
```

## Output Format

By default, the CLI pretty-prints JSON output. To get raw JSON (for piping to other tools):

```bash
./tempo_cli.py search-query '{ .service.name = "my-service" }' --no-pretty | jq .
```

## Error Handling

The CLI provides detailed error messages including:
- HTTP status codes
- API error messages
- Request URLs for debugging

## Help

Get help for any command:

```bash
# General help
./tempo_cli.py --help

# Command-specific help
./tempo_cli.py search-query --help
```
