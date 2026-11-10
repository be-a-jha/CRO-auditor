import { Sandbox } from "@vercel/sandbox";
import { randomUUID } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const PORT = 8787;
const SESSION_MINUTES = 15;
const SESSION_LIMIT = 45 * 60_000;
const STARTUP_TIMEOUT = 150_000;
const sandboxName = process.env.VERCEL_ENV === "preview" ? "cro-auditor-preview" : "cro-auditor-worker";

let source;
let starting;

function workerSource() {
  source ??= (async () => {
    const pythonFiles = (await readdir("cro", { recursive: true }))
      .filter((path) => /\.(py|json|j2)$/.test(path))
      .map((path) => join("cro", path));
    const files = ["app.py", "run.py", "requirements.txt", "scripts/start-vercel-worker.sh", ...pythonFiles];
    return Promise.all(files.map(async (path) => {
      const content = await readFile(path);
      return {
        path: path.replaceAll("\\", "/"),
        content: path.endsWith(".sh") ? Buffer.from(content.toString().replaceAll("\r\n", "\n")) : content,
      };
    }));
  })();
  return source;
}

async function healthy(origin, token) {
  try {
    const response = await fetch(new URL("/api/config", origin), {
      headers: { "X-CRO-Worker-Token": token },
      signal: AbortSignal.timeout(3000),
    });
    await response.body?.cancel();
    return response.ok;
  } catch {
    return false;
  }
}

async function startWorker(token) {
  const sandbox = await Sandbox.getOrCreate({
    name: sandboxName,
    image: "vercel/sandbox/universal",
    ports: [PORT],
    resources: { vcpus: 2 },
    timeout: SESSION_MINUTES * 60_000,
    resume: true,
  });
  const origin = sandbox.domain(PORT);
  if (await healthy(origin, token)) return { sandbox, origin };

  const directory = `cro-releases/${randomUUID()}`;
  const files = await workerSource();
  await sandbox.writeFiles(files.map(({ path, content }) => ({ path: `${directory}/${path}`, content })));
  const command = await sandbox.runCommand({
    cmd: "bash",
    args: [`${directory}/scripts/start-vercel-worker.sh`, directory],
    detached: true,
    env: { CRO_WORKER_TOKEN: token },
  });
  console.info("Starting CRO browser worker", command.cmdId);

  const deadline = Date.now() + STARTUP_TIMEOUT;
  while (Date.now() < deadline) {
    if (await healthy(origin, token)) return { sandbox, origin };
    await delay(1500);
  }
  throw new Error("The browser worker did not become ready.");
}

export function getWorker(token) {
  starting ??= startWorker(token).finally(() => { starting = undefined; });
  return starting;
}

export async function reserveAuditTime(sandbox) {
  const timeout = sandbox.timeout ?? SESSION_MINUTES * 60_000;
  const remaining = timeout - (Date.now() - sandbox.createdAt.getTime());
  const minimum = 8 * 60_000;
  if (remaining >= minimum) return;
  const extension = Math.min(SESSION_LIMIT - timeout, minimum - remaining + 60_000);
  if (extension < minimum - remaining) {
    throw new Error("The browser worker is near its session limit. Retry after it restarts.");
  }
  await sandbox.extendTimeout(extension);
}
