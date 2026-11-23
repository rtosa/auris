import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = dirname(fileURLToPath(import.meta.url));
const distDir = resolve(frontendDir, "dist");
const indexPath = resolve(distDir, "index.html");
let html = readFileSync(indexPath, "utf8");

function inlineAsset(tagPattern, replacement) {
  const match = html.match(tagPattern);
  if (!match) throw new Error(`Built dashboard is missing asset tag: ${tagPattern}`);
  const assetPath = resolve(distDir, match[1].replace(/^\.\//, ""));
  const contents = readFileSync(assetPath, "utf8");
  html = html.replace(match[0], replacement(contents));
}

inlineAsset(
  /<script\b[^>]*\bsrc="([^\"]+\.js)"[^>]*><\/script>/i,
  (javascript) => `<script type="module">\n${javascript.replace(/<\/script/gi, "<\\/script")}\n</script>`,
);
inlineAsset(
  /<link\b[^>]*\brel="stylesheet"[^>]*\bhref="([^\"]+\.css)"[^>]*>/i,
  (stylesheet) => `<style>\n${stylesheet}\n</style>`,
);

writeFileSync(indexPath, html);

