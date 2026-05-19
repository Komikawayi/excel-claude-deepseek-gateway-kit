#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const { existsSync } = require("node:fs");
const { join } = require("node:path");

const repoRoot = process.cwd();
const gatewayDir = join(repoRoot, "gateway_unified");
const isWin = process.platform === "win32";

function run(command, args, cwd = gatewayDir) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    shell: false
  });
  if (result.error) {
    console.error(`[gateway-wrapper] Failed to run: ${command} ${args.join(" ")}`);
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function ensureGatewayDir() {
  if (!existsSync(gatewayDir)) {
    console.error("[gateway-wrapper] Missing directory: gateway_unified/");
    process.exit(1);
  }
}

function help() {
  console.log("Available commands:");
  console.log("  npm run gateway:install     # Create/update .venv and install Python deps");
  console.log("  npm run gateway:start       # Start with run-gateway.ps1 (Windows) or uvicorn (others)");
  console.log("  npm run gateway:start:direct # Start uvicorn directly");
  console.log("  npm run gateway:test        # Run pytest");
}

function install() {
  if (isWin) {
    const venvPython = ".venv\\Scripts\\python.exe";
    if (!existsSync(".venv")) {
      run("python", ["-m", "venv", ".venv"]);
    }
    run(venvPython, ["-m", "pip", "install", "--upgrade", "pip"]);
    run(venvPython, ["-m", "pip", "install", "-e", "."]);
    return;
  }
  run("python3", ["-m", "venv", ".venv"]);
  run(".venv/bin/python", ["-m", "pip", "install", "--upgrade", "pip"]);
  run(".venv/bin/python", ["-m", "pip", "install", "-e", "."]);
}

function start() {
  if (isWin) {
    run("powershell", [
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      ".\\run-gateway.ps1"
    ]);
    return;
  }
  startDirect();
}

function startDirect() {
  if (isWin) {
    const venvPython = ".venv\\Scripts\\python.exe";
    run(venvPython, [
      "-m",
      "uvicorn",
      "claude_gateway.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8790",
      "--no-use-colors"
    ]);
    return;
  }
  run(".venv/bin/python", [
    "-m",
    "uvicorn",
    "claude_gateway.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8790"
  ]);
}

function test() {
  if (isWin) {
    run(".venv\\Scripts\\python.exe", ["-m", "pytest", "tests", "-v"]);
    return;
  }
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
  if (cmd === "start:direct") {
    startDirect();
    return;
  }
  if (cmd === "test") {
    test();
    return;
  }
  console.error(`[gateway-wrapper] Unknown command: ${cmd}`);
  help();
  process.exit(1);
}

main();
