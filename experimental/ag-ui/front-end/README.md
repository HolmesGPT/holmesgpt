# ExampleOps✨

A React TypeScript demo observability application with integrated HolmesGPT AG-UI chat assistant.

![ExampleOps demo video](https://github.com/kylehounslow/holmesgpt/blob/docs/experimental/ag-ui/docs/holmesgpt-agui-demo-1.gif?raw=true)

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. Configure your agent endpoint:
   ```bash
   cp .env.example .env
   # Edit .env to set your AGENT_URL
   ```

3. Start the development server:
   ```bash
   npm start
   ```

## Configuration

The app expects an AG-UI compatible agent service running at the base URL specified in `AGENT_URL`. The chat endpoint will be accessed at `${AGENT_URL}/api/agui/chat`. The agent should:

- Follow the AG-UI Protocol
- Accept POST requests with `RunAgentInput` payload
- Return Server-Sent Events (SSE) stream containing AG-UI events


## AG-UI Integration

The chat assistant uses:

- **HttpAgent**: For HTTP-based agent communication
- **AgentSubscriber**: For handling streaming events
- **Event Types**: TEXT_MESSAGE_START, TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_END, etc.

See the [AG-UI documentation](https://docs.ag-ui.com) for more details.
