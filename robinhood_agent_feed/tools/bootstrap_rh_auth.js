#!/usr/bin/env node
/** Wrapper — AWS SDK lives under lambdas/run_agent; use Python bootstrap. */
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const py = join(__dirname, "bootstrap_rh_auth.py");
const pyExe = process.platform === "win32" ? "py" : "python3";
const r = spawnSync(pyExe, [py, ...process.argv.slice(2)], { stdio: "inherit" });
process.exit(r.status ?? 1);
