import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import { createInterface } from "readline";
import { randomUUID } from "crypto";
import { config } from "./config";
import { logger } from "./logger";
import { ScrapedProfile } from "./types";

interface WorkerResponse {
  id?: string;
  event?: string;
  profile?: ScrapedProfile;
  error?: { code: string; message: string };
  elapsed_ms?: number;
}

interface PendingRequest {
  resolve: (resp: WorkerResponse) => void;
  reject: (err: Error) => void;
  timer: NodeJS.Timeout;
}

class PythonWorker {
  private child: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<string, PendingRequest>();
  private ready = false;
  private readyPromise: Promise<void> | null = null;
  private restartCount = 0;
  private busy = false;

  start(): Promise<void> {
    if (this.readyPromise) return this.readyPromise;
    this.readyPromise = this.spawn();
    return this.readyPromise;
  }

  private spawn(): Promise<void> {
    const args = [
      config.PYTHON_WORKER_PATH,
      "--timeout",
      String(Math.floor(config.NAV_TIMEOUT_MS / 1000)),
      "--user-agent",
      config.USER_AGENT,
    ];
    if (config.EXECUTABLE_PATH) {
      args.push("--executable-path", config.EXECUTABLE_PATH);
    }
    if (config.TIKTOK_COOKIES_PATH) {
      args.push("--cookies-path", config.TIKTOK_COOKIES_PATH);
    }

    logger.info({ args }, "spawning python worker");
    const child = spawn(config.PYTHON_BIN, args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });
    this.child = child;

    child.stderr.on("data", (d: Buffer) => {
      logger.debug({ src: "py" }, d.toString("utf8").trimEnd());
    });

    const rl = createInterface({ input: child.stdout });
    rl.on("line", (line) => this.handleLine(line));

    child.on("exit", (code, signal) => {
      logger.warn({ code, signal }, "python worker exited");
      this.failAllPending(
        new Error(`worker exited code=${code} signal=${signal}`),
      );
      this.ready = false;
      this.child = null;
      this.readyPromise = null;
      if (this.restartCount < 10) {
        this.restartCount++;
        setTimeout(
          () => void this.start(),
          Math.min(5000, 500 * this.restartCount),
        );
      }
    });

    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!this.ready) reject(new Error("worker ready timeout"));
      }, 30_000);
      this.onReady = () => {
        clearTimeout(timer);
        this.ready = true;
        this.restartCount = 0;
        resolve();
      };
    });
  }

  private onReady: () => void = () => undefined;

  private handleLine(line: string): void {
    if (!line.trim()) return;
    let resp: WorkerResponse;
    try {
      resp = JSON.parse(line) as WorkerResponse;
    } catch (err) {
      logger.warn({ line, err }, "invalid worker output");
      return;
    }
    if (resp.event === "ready") {
      this.onReady();
      return;
    }
    const id = resp.id;
    if (!id) return;
    const pending = this.pending.get(id);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pending.delete(id);
    this.busy = false;
    pending.resolve(resp);
  }

  private failAllPending(err: Error): void {
    for (const [id, p] of this.pending.entries()) {
      clearTimeout(p.timer);
      p.reject(err);
      this.pending.delete(id);
    }
    this.busy = false;
  }

  async request(username: string): Promise<WorkerResponse> {
    await this.start();
    if (!this.child || !this.ready) {
      throw new Error("worker not ready");
    }

    const id = randomUUID();
    const payload = JSON.stringify({ id, username }) + "\n";

    return new Promise<WorkerResponse>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.busy = false;
        reject(new Error("request timeout"));
      }, config.REQUEST_TIMEOUT_MS);

      this.pending.set(id, { resolve, reject, timer });
      this.busy = true;
      this.child!.stdin.write(payload, (err) => {
        if (err) {
          clearTimeout(timer);
          this.pending.delete(id);
          this.busy = false;
          reject(err);
        }
      });
    });
  }

  isBusy(): boolean {
    return this.busy;
  }

  shutdown(): void {
    if (this.child) {
      this.child.stdin.end();
      const child = this.child;
      setTimeout(() => {
        if (!child.killed) child.kill("SIGKILL");
      }, 3000).unref();
    }
  }
}

class WorkerPool {
  private workers: PythonWorker[];
  private rr = 0;

  constructor(size: number) {
    this.workers = Array.from({ length: size }, () => new PythonWorker());
  }

  async start(): Promise<void> {
    await Promise.all(this.workers.map((w) => w.start()));
  }

  async request(username: string): Promise<WorkerResponse> {
    const idle = this.workers.find((w) => !w.isBusy());
    const w = idle ?? this.workers[this.rr++ % this.workers.length];
    return w.request(username);
  }

  shutdown(): void {
    this.workers.forEach((w) => w.shutdown());
  }
}

export const workerPool = new WorkerPool(config.MAX_CONCURRENCY);
