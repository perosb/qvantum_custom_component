/**
 * Repo Lookup Extension (pi-review)
 *
 * Two read-only, checkout-scoped tools for the PR reviewer: `repo_grep` and
 * `repo_read`.
 *
 * The reviewer runs without pi's built-in tools so that a prompt-injected diff
 * cannot read credentials or run commands. That safety has a cost: the model
 * only sees the diff, so it cannot tell whether a symbol, file or call site
 * already exists — and "not in the diff" is not "not in the repository". These
 * tools cover exactly that lookup while staying inside the working tree: no
 * child processes, no network, no paths outside the checkout.
 *
 * Set PI_REVIEW_ROOT to pin the root; it defaults to the working directory
 * (the checkout in CI).
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";

const REPO_ROOT = resolve(process.env.PI_REVIEW_ROOT || process.cwd());

/** Directories that never hold reviewable source. */
const SKIP_DIRS = new Set([
  ".git",
  ".venv",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
  "__pycache__",
  "build",
  "dist",
  "htmlcov",
  "node_modules",
]);

const MAX_FILES = 8000;
const MAX_FILE_BYTES = 2_000_000;
const MAX_RESULTS = 60;
const MAX_READ_LINES = 400;
const MAX_OUTPUT_CHARS = 14_000;

/** Resolve a repo-relative path, rejecting anything outside the checkout. */
function scopedPath(input: string | undefined): string | undefined {
  if (!input || input.trim() === "" || input === ".") return REPO_ROOT;
  const absolute = resolve(REPO_ROOT, input);
  if (absolute !== REPO_ROOT && !absolute.startsWith(REPO_ROOT + sep)) {
    return undefined;
  }
  return absolute;
}

function collectFiles(target: string, out: string[]): void {
  let stats;
  try {
    stats = statSync(target);
  } catch {
    return;
  }
  if (stats.isFile()) {
    out.push(target);
    return;
  }
  if (!stats.isDirectory()) return;

  let entries;
  try {
    entries = readdirSync(target, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (out.length >= MAX_FILES) return;
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      collectFiles(join(target, entry.name), out);
    } else if (entry.isFile()) {
      out.push(join(target, entry.name));
    }
  }
}

/** Read a text file, or undefined for binary/oversized/unreadable files. */
function readTextFile(path: string): string | undefined {
  try {
    if (statSync(path).size > MAX_FILE_BYTES) return undefined;
    const text = readFileSync(path, "utf8");
    return text.includes("\u0000") ? undefined : text;
  } catch {
    return undefined;
  }
}

function rel(path: string): string {
  return relative(REPO_ROOT, path).split(sep).join("/");
}

export default function repoLookupExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: "repo_grep",
    label: "Repo grep",
    description:
      "Search the checked-out repository for a regular expression, one match per line, with repo-relative paths and line numbers. Read-only and confined to the checkout. Use it to check whether a symbol, file, call site, translation key or entity already exists before reporting it as missing.",
    promptSnippet:
      "Search the checked-out repository (regex, repo-scoped) to verify that code exists before flagging it as missing",
    promptGuidelines: [
      "Call repo_grep (or repo_read) before reporting that a function, file, call site, translation key, entity or wiring is missing: absence in the diff is not absence in the repository.",
    ],
    parameters: Type.Object({
      pattern: Type.String({
        description:
          "JavaScript regular expression matched against each line, e.g. \"def resolve_indoor_metric_key\".",
      }),
      path: Type.Optional(
        Type.String({
          description:
            "Repo-relative file or directory to search (default: the whole checkout).",
        }),
      ),
      max_results: Type.Optional(
        Type.Number({
          description: `Maximum matching lines to return (default ${MAX_RESULTS}).`,
        }),
      ),
    }),
    annotations: { readOnlyHint: true, openWorldHint: false },
    async execute(_toolCallId, params) {
      const target = scopedPath(params.path);
      if (target === undefined) {
        throw new Error("path escapes the repository checkout");
      }
      let pattern: RegExp;
      try {
        pattern = new RegExp(params.pattern);
      } catch (error) {
        throw new Error(`invalid regular expression: ${String(error)}`);
      }
      const limit = Math.min(
        Math.max(1, Math.trunc(params.max_results ?? MAX_RESULTS)),
        500,
      );

      const files: string[] = [];
      collectFiles(target, files);

      const matches: string[] = [];
      let scanned = 0;
      for (const file of files) {
        if (matches.length >= limit) break;
        const text = readTextFile(file);
        if (text === undefined) continue;
        scanned += 1;
        const lines = text.split("\n");
        for (let index = 0; index < lines.length; index += 1) {
          if (!pattern.test(lines[index])) continue;
          matches.push(`${rel(file)}:${index + 1}: ${lines[index].trimEnd()}`);
          if (matches.length >= limit) break;
        }
      }

      const header =
        matches.length === 0
          ? `No match for /${params.pattern}/ under ${rel(target) || "."} (${scanned} files scanned).`
          : `${matches.length} match(es) for /${params.pattern}/ under ${rel(target) || "."}:`;
      let body = [header, ...matches].join("\n");
      if (body.length > MAX_OUTPUT_CHARS) {
        body = `${body.slice(0, MAX_OUTPUT_CHARS)}\n… truncated; narrow the pattern or path.`;
      }
      return { content: [{ type: "text", text: body }], details: undefined };
    },
  });

  pi.registerTool({
    name: "repo_read",
    label: "Repo read",
    description:
      "Read a file from the checked-out repository with line numbers. Read-only and confined to the checkout. Use it to confirm how existing code, a resolver or a call site actually works before flagging the diff.",
    promptSnippet:
      "Read a file from the checked-out repository (read-only, repo-scoped)",
    promptGuidelines: [
      "Call repo_read when a review claim depends on how existing, unchanged code behaves.",
    ],
    parameters: Type.Object({
      path: Type.String({
        description: "Repo-relative file path, e.g. custom_components/qvantum/statistics.py.",
      }),
      start_line: Type.Optional(
        Type.Number({ description: "First line to return (1-based, default 1)." }),
      ),
      limit: Type.Optional(
        Type.Number({
          description: `Maximum lines to return (default ${MAX_READ_LINES}).`,
        }),
      ),
    }),
    annotations: { readOnlyHint: true, openWorldHint: false },
    async execute(_toolCallId, params) {
      const target = scopedPath(params.path);
      if (target === undefined) {
        throw new Error("path escapes the repository checkout");
      }
      const text = readTextFile(target);
      if (text === undefined) {
        throw new Error(`not a readable text file: ${params.path}`);
      }
      const start = Math.max(1, Math.trunc(params.start_line ?? 1));
      const limit = Math.min(
        Math.max(1, Math.trunc(params.limit ?? MAX_READ_LINES)),
        1000,
      );
      const lines = text.split("\n");
      const slice = lines.slice(start - 1, start - 1 + limit);
      const numbered = slice.map(
        (line, index) => `${start + index}: ${line}`,
      );
      const suffix =
        start - 1 + limit < lines.length
          ? `\n… ${lines.length - (start - 1 + limit)} more line(s); continue with start_line=${start + limit}.`
          : "";
      let body = `${rel(target)} (${lines.length} lines)\n${numbered.join("\n")}${suffix}`;
      if (body.length > MAX_OUTPUT_CHARS) {
        body = `${body.slice(0, MAX_OUTPUT_CHARS)}\n… truncated; use start_line/limit.`;
      }
      return { content: [{ type: "text", text: body }], details: undefined };
    },
  });
}
