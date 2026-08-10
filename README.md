# A2A OPC

OPC Link is a mobile-first prototype for matching One Person Companies. Each
OPC has a long-lived Agent that can perform bounded screening and coordinate a
meeting through the A2A protocol.

## Included

- `opc-agent-platform/`: FastAPI backend, A2A Agent Cards, calendar tool,
  DeepSeek integration, and tests.
- `opc-link-prototype/`: mobile interaction prototype using the supplied video
  assets in `视频/`.
- `practice/`: minimal JSON-RPC A2A client experiment.
- `A2A实践报告.md`: protocol analysis and practice notes.

## Run locally

```bash
cd opc-agent-platform
cp .env.example .env
# Set DEEPSEEK_API_KEY in .env when using live model responses.
uv sync
uv run opc-agent-platform
```

Open <http://127.0.0.1:8010/app/>.

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
