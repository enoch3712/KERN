import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = process.cwd();
const output = resolve(root, ".open-next");
const assets = resolve(output, "assets");

await rm(output, { recursive: true, force: true });
await mkdir(assets, { recursive: true });
await cp(resolve(root, "out"), assets, { recursive: true });

const worker = await readFile(resolve(root, "sites-worker.js"), "utf8");
await writeFile(resolve(output, "worker.js"), worker, "utf8");

console.log("Sites bundle ready: .open-next/worker.js + .open-next/assets");
