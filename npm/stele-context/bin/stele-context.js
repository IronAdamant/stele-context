#!/usr/bin/env node
"use strict";

const { spawn } = require("child_process");
const path = require("path");

const target = (() => {
  if (process.platform === "linux" && process.arch === "x64") return "x86_64-unknown-linux-musl";
  if (process.platform === "linux" && process.arch === "arm64") return "aarch64-unknown-linux-gnu";
  if (process.platform === "darwin" && process.arch === "arm64") return "aarch64-apple-darwin";
  if (process.platform === "darwin" && process.arch === "x64") return "x86_64-apple-darwin";
  if (process.platform === "win32" && process.arch === "x64") return "x86_64-pc-windows-msvc";
  if (process.platform === "win32" && process.arch === "arm64") return "aarch64-pc-windows-msvc";
  return "";
})();

const ext = process.platform === "win32" ? ".exe" : "";
const local = target
  ? path.join(__dirname, "..", "vendor", `stele-context-${target}${ext}`)
  : "";
const bin = process.env.STELE_CONTEXT_BIN || local || "stele-context";
const child = spawn(bin, process.argv.slice(2), { stdio: "inherit" });
child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  process.exit(code == null ? 1 : code);
});
child.on("error", () => {
  process.stderr.write(
    "stele-context binary not found. Install a GitHub Release asset or set STELE_CONTEXT_BIN.\n"
  );
  process.exit(1);
});
