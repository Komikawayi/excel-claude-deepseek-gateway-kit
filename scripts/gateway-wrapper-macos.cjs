#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const { existsSync } = require("node:fs");
const { join } = require("node:path");

const repoRoot = process.cwd();
const gatewayDir = join(repoRoot, "gateway_macos");

function run(command, args, cwd = gatewayDir) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    shell: false
  });
  if (result.error) {
    console.error(`[gateway-wrapper-macos] Failed to run: ${command} ${args.join(" ")}`);
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function ensureGatewayDir() {
  if (!existsSync(gatewayDir)) {
    console.error("[gateway-wrapper-macos] Missing directory: gateway_macos/");
    process.exit(1);
  }
}

function help() {
  console.log("Available commands:");
  console.log("  npm run gateway:macos:install  # Create/update .venv and install Python deps");
  console.log("  npm run gateway:macos:start    # Start the macOS gateway on port 8890");
  console.log("  npm run gateway:macos:test     # Run pytest");
}

function install() {
  run("python3", ["-m", "venv", ".venv"]);
  run(".venv/bin/python", ["-m", "pip", "install", "--upgrade", "pip"]);
  run(".venv/bin/python", ["-m", "pip", "install", "-e", ".[dev]"]);
}

function start() {
  run(".venv/bin/python", [
    "-m",
    "uvicorn",
    "claude_gateway_macos.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8890"
  ]);
}

function test() {
  run(".venv/bin/python", ["-m", "pytest", "tests", "-v"]);
}

function main() {
  ensureGatewayDir();
  const cmd = process.argv[2] || "help";
  if (cmd === "help") {
    help();
    return;
  }
  if (cmd === "install") {
    install();
    return;
  }
  if (cmd === "start") {
    start();
    return;
  }
  if (cmd === "test") {
    test();
    return;
  }
  console.error(`[gateway-wrapper-macos] Unknown command: ${cmd}`);
  help();
  process.exit(1);
}

main();
