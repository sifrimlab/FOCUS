#!/usr/bin/env node
/**
 * Design-system guard (gui_src/DESIGN.md 11).
 *
 * Fails when a .vue file uses raw Tailwind palette classes, `dark:` color
 * variants, or hex colors. Colors must come from semantic tokens.
 * Usage: node check-design.mjs <dir> [<dir> ...]   (directories relative to cwd)
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const ROOTS = process.argv.slice(2).map(dir => resolve(dir));
if (ROOTS.length === 0) {
  console.error('Usage: check-design.mjs <dir> [<dir> ...]');
  process.exit(2);
}

const PALETTE = 'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|black|white';
const UTILITY = 'text|bg|border|ring|outline|fill|stroke|from|via|to|divide|placeholder|accent|caret|decoration|shadow';

const RULES = [
  {
    name: 'raw palette class',
    pattern: new RegExp(`\\b(?:${UTILITY})-(?:${PALETTE})(?:-\\d{2,3})?(?:/\\d+)?\\b`, 'g'),
  },
  {
    name: 'dark: color variant',
    pattern: new RegExp(`\\bdark:(?:hover:|focus:)?(?:${UTILITY})-`, 'g'),
  },
  {
    name: 'hex color',
    pattern: /(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b/g,
  },
];

function* vueFiles(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* vueFiles(path);
    else if (name.endsWith('.vue')) yield path;
  }
}

let violations = 0;
for (const file of ROOTS.flatMap(root => [...vueFiles(root)])) {
  const lines = readFileSync(file, 'utf8').split('\n');
  lines.forEach((line, i) => {
    for (const { name, pattern } of RULES) {
      for (const match of line.matchAll(pattern)) {
        violations += 1;
        console.error(`${relative(process.cwd(), file)}:${i + 1}  ${name}: ${match[0]}`);
      }
    }
  });
}

if (violations > 0) {
  console.error(`\n${violations} design-system violation(s). Use semantic tokens (see gui_src/DESIGN.md).`);
  process.exit(1);
}
console.log('Design check passed: no raw palette classes, dark: color variants, or hex colors.');
