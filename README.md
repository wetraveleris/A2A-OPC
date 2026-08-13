# A2A OPC

OPC Link is a mobile-first prototype for matching One Person Companies. Each
OPC has a long-lived Agent that can perform bounded screening and coordinate a
meeting through the A2A protocol.

## Included

- `opc-agent-platform/`: FastAPI backend, A2A Agent Cards, calendar tool,
  Ollama/DeepSeek integration, public-internet A2A demo, and tests.
- `opc-link-prototype/`: mobile interaction prototype using the supplied video
  assets in `视频/`.
- `practice/`: minimal JSON-RPC A2A client experiment.
- `A2A实践报告.md`: protocol analysis and practice notes.

## Run locally

```bash
cd opc-agent-platform
cp .env.example .env
# The default .env.example uses local Ollama with qwen3:4b.
uv sync
uv run opc-agent-platform
```

Open <http://127.0.0.1:8010/app/>.

The mobile prototype includes a "公网" tab. It sends an approved request from
the local OPC Agent to the public third-party Perkoon Agent on the internet through A2A,
then shows the returned Task ID, state, remote URL, skill, and Artifact text.
The same tab also includes a two-Agent debugger: independent Agent identities,
shared recent chat history, and expandable request/response evidence for every turn.
It creates separate tokenized pages for participant A and B. In human-approval
mode, each owner edits or approves their Agent's private draft. In takeover mode,
the backend Agents chat automatically while both owners can observe, intervene, or stop them.

## Verify

```bash
cd opc-agent-platform
uv run pytest -q

cd ../opc-link-prototype
npm install
npm run test:e2e
```

The application intentionally excludes local keys, `.env` files, virtual
environments, dependency directories, generated screenshots, and upstream A2A
reference clones.
