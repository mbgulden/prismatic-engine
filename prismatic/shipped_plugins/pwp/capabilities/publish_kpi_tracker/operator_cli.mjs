#!/usr/bin/env node
//
// Node-side wrapper for the PWP publish-kpi-tracker operator CLI.
//
// Prismatic Engine cron jobs run on Python; this shim lets the dashboard
// pipeline stay in one place when triggered by a Node caller, while still
// delegating to the canonical Python implementation.
//
// Subcommands mirror operator_cli.py:
//   node operator_cli.mjs list-sites
//   node operator_cli.mjs validate
//   node operator_cli.mjs show <slug>
//   node operator_cli.mjs build-dashboard --publish-root <dir> --window last24h
//
// Environment:
//   PWP_REPO_ROOT       optional; defaults to the inferred path
//
// Calls python3, which is the standard Prismatic runtime.

import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

const HERE = dirname(fileURLToPath(import.meta.url));
const PYTHON = process.env.PWP_KPI_PYTHON || 'python3';

function resolveCliScript() {
  return resolve(HERE, 'operator_cli.py');
}

function findPwpRepo(cliScriptPath) {
  // Layout: <PWP_REPO>/prismatic/shipped_plugins/pwp/capabilities/publish_kpi_tracker/operator_cli.py
  // Five parents up from operator_cli.py lands on PWP_REPO.
  // Use process.cwd() as a fallback if the relative path doesn't resolve.
  let p = cliScriptPath;
  for (let i = 0; i < 6; i++) p = dirname(p);
  if (existsSync(p)) return p;
  return process.cwd();
}

const cli = resolveCliScript();
const pwpRepo = process.env.PWP_REPO_ROOT || findPwpRepo(cli);
if (!existsSync(cli)) {
  console.error(`kpi.mjs: cannot find operator_cli.py at ${cli}`);
  process.exit(2);
}

const args = process.argv.slice(2);
const result = spawnSync(PYTHON, [cli, ...args], {
  stdio: 'inherit',
  cwd: pwpRepo,
  env: { ...process.env, PWP_REPO_ROOT: pwpRepo },
});
process.exit(result.status === null ? 1 : result.status);
