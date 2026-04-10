import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..");
const webDir = path.join(root, "web");
const distDir = path.join(root, "dist");
const dataSrc = path.join(root, "data");
const dataDst = path.join(distDir, "data");

function copyRecursive(src, dest) {
  if (!fs.existsSync(src)) return;
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.mkdirSync(dest, { recursive: true });
    for (const name of fs.readdirSync(src)) {
      copyRecursive(path.join(src, name), path.join(dest, name));
    }
  } else {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
  }
}

fs.rmSync(distDir, { recursive: true, force: true });
fs.mkdirSync(distDir, { recursive: true });
copyRecursive(webDir, distDir);
fs.mkdirSync(dataDst, { recursive: true });
if (fs.existsSync(dataSrc)) {
  for (const name of fs.readdirSync(dataSrc)) {
    if (!name.endsWith(".json") && !name.endsWith(".txt")) continue;
    fs.copyFileSync(path.join(dataSrc, name), path.join(dataDst, name));
  }
}
console.log("Built dist/ with web/ + data/*.json");
