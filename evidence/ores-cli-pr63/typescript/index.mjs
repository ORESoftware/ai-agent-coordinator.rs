#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { Node, Project, SyntaxKind } from 'ts-morph';
import pkg from './package.json' with { type: 'json' };

const SCHEMA = 'ores.code-config-audit.v1';
const MAX_FILES = 4096;
const MAX_BYTES = 64 * 1024 * 1024;
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const SKIP = new Set([
  '.git', '.next', '.turbo', '.zed', 'build', 'coverage', 'dist', 'generated',
  'node_modules', 'target', 'vendor', 'zed_modules',
]);
const EXTENSIONS = new Set(['.ts', '.tsx', '.mts', '.cts']);
const ENV_ROOTS = new Set(['process.env', 'Bun.env']);
const ENV_KEY = /^[A-Z_][A-Z0-9_]*$/;

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(2);
}

function args() {
  const values = new Map();
  for (let i = 2; i < process.argv.length; i += 1) {
    const key = process.argv[i];
    if (!key.startsWith('--') || i + 1 >= process.argv.length) fail('invalid arguments');
    values.set(key, process.argv[i + 1]);
    i += 1;
  }
  if (values.get('--format') !== 'json') fail('only --format json is supported');
  const root = values.get('--root');
  if (!root) fail('--root is required');
  return { root: fs.realpathSync(root) };
}

function inventory(root) {
  const files = [];
  let totalBytes = 0;
  function walk(dir) {
    const entries = fs.readdirSync(dir, { withFileTypes: true })
      .sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      if (entry.isSymbolicLink()) continue;
      const absolute = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!SKIP.has(entry.name)) walk(absolute);
        continue;
      }
      if (!entry.isFile() || !EXTENSIONS.has(path.extname(entry.name))) continue;
      const size = fs.statSync(absolute).size;
      if (size > MAX_FILE_BYTES) fail('source file exceeds bound');
      totalBytes += size;
      if (totalBytes > MAX_BYTES || files.length >= MAX_FILES) fail('source inventory exceeds bound');
      files.push(absolute);
    }
  }
  walk(root);
  return files;
}

function relative(root, file) {
  const rel = path.relative(root, file).split(path.sep).join('/');
  if (!rel || rel.startsWith('../') || path.isAbsolute(rel)) fail('source escaped root');
  return rel;
}

function location(sourceFile, nodeOrPos) {
  const pos = typeof nodeOrPos === 'number' ? nodeOrPos : nodeOrPos.getStart();
  const point = sourceFile.getLineAndColumnAtPos(pos);
  return { line: point.line, column: point.column };
}

function event(kind, value, sourceFile, nodeOrPos) {
  const loc = location(sourceFile, nodeOrPos);
  return value === undefined ? { kind, ...loc } : { kind, value, ...loc };
}

function dedupe(events) {
  const seen = new Set();
  return events.filter((item) => {
    const key = JSON.stringify(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => a.line - b.line || a.column - b.column || a.kind.localeCompare(b.kind));
}

function regexLane(text, sourceFile) {
  const events = [];
  const patterns = [
    ['config-literal', /["'`]([^"'`\n]*\.toml)["'`]/g, 1],
    ['env-api', /\b(process\.env|Deno\.env\.get|Bun\.env)\b/g, 1],
    ['toml-api', /\b((?:TOML|toml)\.(?:parse|parseString|decode)|parseToml)\b/g, 1],
  ];
  for (const [kind, re, group] of patterns) {
    for (const match of text.matchAll(re)) {
      events.push(event(kind, match[group], sourceFile, match.index ?? 0));
    }
  }
  return dedupe(events);
}

function stringArgument(node) {
  if (!node) return undefined;
  if (Node.isStringLiteral(node) || Node.isNoSubstitutionTemplateLiteral(node)) {
    return node.getLiteralValue();
  }
  return undefined;
}

function bindingKey(element) {
  if (element.getDotDotDotToken()) return undefined;
  const property = element.getPropertyNameNode();
  if (!property) {
    const name = element.getNameNode();
    return Node.isIdentifier(name) ? name.getText() : undefined;
  }
  if (Node.isIdentifier(property)) return property.getText();
  if (Node.isStringLiteral(property) || Node.isNoSubstitutionTemplateLiteral(property)) {
    return property.getLiteralValue();
  }
  return undefined;
}

function astLane(sourceFile) {
  const events = [];

  for (const access of sourceFile.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression)) {
    const text = access.getText();
    const match = /^process\.env\.([A-Z_][A-Z0-9_]*)$/.exec(text)
      ?? /^Bun\.env\.([A-Z_][A-Z0-9_]*)$/.exec(text);
    if (match) events.push(event('env-read', match[1], sourceFile, access));
  }

  for (const access of sourceFile.getDescendantsOfKind(SyntaxKind.ElementAccessExpression)) {
    const target = access.getExpression().getText();
    if (!ENV_ROOTS.has(target)) continue;
    const value = stringArgument(access.getArgumentExpression());
    events.push(event('env-read', value, sourceFile, access));
  }

  for (const variable of sourceFile.getDescendantsOfKind(SyntaxKind.VariableDeclaration)) {
    const initializer = variable.getInitializer();
    if (!initializer || !ENV_ROOTS.has(initializer.getText())) continue;
    const name = variable.getNameNode();
    if (!Node.isObjectBindingPattern(name)) continue;
    for (const element of name.getElements()) {
      const key = bindingKey(element);
      if (!key || !ENV_KEY.test(key)) continue;
      events.push(event('env-read', key, sourceFile, element.getPropertyNameNode() ?? element));
    }
  }

  for (const call of sourceFile.getDescendantsOfKind(SyntaxKind.CallExpression)) {
    const callee = call.getExpression().getText();
    const first = stringArgument(call.getArguments()[0]);

    if (callee === 'Deno.env.get') {
      events.push(event('env-read', first, sourceFile, call));
    }

    if (first?.endsWith('.toml') && /(?:readFileSync|readFile|readTextFile|Bun\.file|Deno\.readTextFile|open)$/.test(callee)) {
      events.push(event('config-read', first, sourceFile, call));
    }

    if (/^(?:(?:TOML|toml)\.(?:parse|parseString|decode)|parseToml)$/.test(callee)) {
      events.push(event('toml-parse', undefined, sourceFile, call));
    }
  }

  return dedupe(events);
}

function main() {
  const { root } = args();
  const files = inventory(root);
  const project = new Project({
    skipAddingFilesFromTsConfig: true,
    compilerOptions: {
      allowJs: false,
      noEmit: true,
      skipLibCheck: true,
    },
  });

  const receipts = files.map((file) => {
    const sourceFile = project.addSourceFileAtPath(file);
    const text = sourceFile.getFullText();
    const parseDiagnostics = sourceFile.compilerNode.parseDiagnostics ?? [];
    return {
      path: relative(root, file),
      syntaxValid: parseDiagnostics.length === 0,
      regexHits: regexLane(text, sourceFile),
      astEvents: astLane(sourceFile),
    };
  }).sort((a, b) => a.path.localeCompare(b.path));

  process.stdout.write(`${JSON.stringify({
    schemaVersion: SCHEMA,
    language: 'typescript',
    parser: 'ts-morph',
    parserVersion: pkg.dependencies['ts-morph'],
    files: receipts,
  })}\n`);
}

main();