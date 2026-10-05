import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const requireFromDashboard = createRequire(path.join(root, "dashboard", "package.json"));

let ts;
try {
  ts = requireFromDashboard("typescript");
} catch {
  process.stderr.write("typescript is not installed in dashboard/. Run the install task first.\n");
  process.exit(2);
}

const scriptKinds = {
  ".js": ts.ScriptKind.JS,
  ".mjs": ts.ScriptKind.JS,
  ".cjs": ts.ScriptKind.JS,
  ".jsx": ts.ScriptKind.JSX,
  ".ts": ts.ScriptKind.TS,
  ".mts": ts.ScriptKind.TS,
  ".cts": ts.ScriptKind.TS,
  ".tsx": ts.ScriptKind.TSX,
};

function commentLines(file) {
  const text = readFileSync(file, "utf8");
  const kind = scriptKinds[path.extname(file)] ?? ts.ScriptKind.TS;
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const seen = new Set();
  const lines = new Set();
  const collect = (ranges) => {
    for (const range of ranges ?? []) {
      if (seen.has(range.pos)) continue;
      seen.add(range.pos);
      lines.add(source.getLineAndCharacterOfPosition(range.pos).line + 1);
    }
  };
  const visit = (node) => {
    collect(ts.getLeadingCommentRanges(text, node.getFullStart()));
    collect(ts.getTrailingCommentRanges(text, node.getEnd()));
    ts.forEachChild(node, visit);
  };
  visit(source);
  return [...lines].sort((left, right) => left - right);
}

const report = {};
for (const file of process.argv.slice(2)) {
  const lines = commentLines(file);
  if (lines.length > 0) report[file] = lines;
}
process.stdout.write(JSON.stringify(report));
