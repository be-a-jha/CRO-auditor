import { copyFile, cp, mkdir } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const output = new URL("dist/", root);

await mkdir(output, { recursive: true });
await copyFile(new URL("ui/index.html", root), new URL("index.html", output));
await cp(new URL("sample_output/", root), new URL("sample/", output), { recursive: true });
