# Repository Overview

This repository now keeps only one maintained implementation:

- `gateway_unified/` (single Anthropic-compatible gateway with provider routing)

Legacy duplicated folders have been removed to keep the repo easier to maintain.

## Quick Start

```bash
cd gateway_unified
pip install -e .
cp .env.example .env
```

Start gateway:

```bash
cd gateway_unified
claude-gateway --provider deepseek --port 8790
```

or

```bash
cd gateway_unified
uvicorn --app-dir src claude_gateway.main:app --host 127.0.0.1 --port 8790
```

For full docs, see:

- `gateway_unified/README.md`
