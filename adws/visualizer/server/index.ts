/**
 * SSSF visualizer server — JSON API over a target repo's sssf.db, plus the
 * built UI when ./dist exists. Archiving sets one review flag; refreshing a
 * report runs the read-only status ADW and publishes a new trace and snapshot.
 *
 * There is no ingest endpoint and no websocket. The data path is
 * agents → sqlite → web ui, and the UI gets there by polling.
 *
 *   bun run server/index.ts
 *   bun run server/index.ts --db /path/to/repo/adws/adw_data/sssf.db
 *   SSSF_DB=/path/to/sssf.db PORT=4600 bun run server/index.ts
 */
import { existsSync, statSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { SssfDb, resolveDbPath } from "./db.ts";
import type { AgentPrompts, ApiError, HealthResponse } from "../shared/types.ts";
import { consoleMessages, linkedPdfs, pendingQuestion, renderQuestionPage } from './pending-question';
import { pdfResponse, receiptResponse } from './receipt';
import { pdfRequest, readPdf, renderPdfInfo } from './pdf-evidence';
import { remainingWorkResponse } from './reports';
import { markdownResponse } from './reports/markdown';
import { expenseDocumentResponse } from './reports/expenses';
import { financeRoot } from './paths';
import { ReportRefresher } from './reports/refresh';

const PORT = Number(process.env.PORT ?? 4613);
const DIST_DIR = resolve(import.meta.dir, "..", "dist");
const FINANCE_ROOT = financeRoot(resolve(import.meta.dir, '..'), process.env.ROL_FINANCE_ROOT);

const dbPath = resolveDbPath();
let db: SssfDb;
try {
  db = new SssfDb(dbPath);
} catch (error) {
  console.error(`[sssf] ${(error as Error).message}`);
  process.exit(1);
}
const reportRefresher = new ReportRefresher(FINANCE_ROOT, db.path, db.sessionsDir);

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function notFound(message: string): Response {
  return json({ error: message } satisfies ApiError, 404);
}

/** Guard every handler so a malformed query can't take the server down mid-run. */
function safely(
  handler: (req: Request) => Response | Promise<Response>,
): (req: Request) => Promise<Response> {
  return async (req) => {
    try {
      return await handler(req);
    } catch (error) {
      console.error(`[sssf] ${req.method} ${new URL(req.url).pathname}:`, error);
      return json({ error: (error as Error).message } satisfies ApiError, 500);
    }
  };
}

/**
 * adw_ids and agent names are path segments on disk, so anything that isn't a
 * plain identifier is rejected outright rather than sanitized into something
 * that might still escape the sessions directory.
 */
const SAFE_SEGMENT = /^[A-Za-z0-9._-]+$/;

function isSafeSegment(value: string): boolean {
  return SAFE_SEGMENT.test(value) && value !== "." && value !== "..";
}

function param(req: Request, key: string): string {
  return decodeURIComponent(
    (req as Request & { params: Record<string, string> }).params[key] ?? "",
  );
}

function intQuery(req: Request, key: string, fallback: number): number {
  const raw = new URL(req.url).searchParams.get(key);
  if (raw === null || raw.trim() === "") return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Serve the built SPA if it has been built; otherwise point at the dev server. */
async function serveStatic(req: Request): Promise<Response> {
  const { pathname } = new URL(req.url);

  if (!existsSync(DIST_DIR)) {
    return new Response(
      `SSSF visualizer API is running on :${PORT}.\n\n` +
        `No ./dist build found. Run "bun run dev" for the Vite dev server ` +
        `(it proxies /api here), or "bun run build" to serve the UI from this process.\n`,
      { status: 200, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  }

  // Reject traversal before touching the filesystem.
  const candidate = resolve(join(DIST_DIR, pathname));
  if (candidate === DIST_DIR || candidate.startsWith(DIST_DIR + "/")) {
    if (existsSync(candidate) && statSync(candidate).isFile()) {
      return new Response(Bun.file(candidate));
    }
  }

  // SPA fallback: breadcrumb routes are client-side.
  const indexHtml = join(DIST_DIR, "index.html");
  if (existsSync(indexHtml)) {
    return new Response(Bun.file(indexHtml), {
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  }
  return notFound("not found");
}

/** The caller supplies a session id only; PDF paths come from its recorded payment. */
async function paymentPdf(req: Request, inspect: boolean): Promise<Response> {
  const adwId = param(req, 'adw_id');
  if (!isSafeSegment(adwId)) return json({ error: 'invalid adw_id' }, 400);
  const session = db.session(adwId);
  if (!session) return notFound(`no session ${adwId}`);
  const messages: string[] = [];
  let cursor = 0;
  let more = true;
  while (more) {
    const page = db.events(adwId, cursor, 1000);
    messages.push(...consoleMessages(page.events));
    cursor = page.cursor;
    more = page.has_more;
  }
  const question = pendingQuestion(messages, session.status);
  const request = pdfRequest(question);
  if (!request) return notFound('No payment PDF context is available for this question.');
  const root = FINANCE_ROOT;
  const result = await readPdf(request, root, inspect);
  if (!inspect) return pdfResponse(result.check_image_pdf, root);
  return new Response(renderPdfInfo(result, adwId, question?.question ?? ''), {
    headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store',
      'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
      'x-content-type-options': 'nosniff' },
  });
}

const server = Bun.serve({
  port: PORT,
  idleTimeout: 90,
  routes: {
    "/api/reports/refresh.js": {
      GET: () => new Response(Bun.file(resolve(import.meta.dir, 'reports/refresh-client.js')), {
        headers: { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' },
      }),
    },

    "/api/sessions/:adw_id/reports/refresh": {
      POST: safely(async req => {
        const adwId = param(req, 'adw_id');
        if (!isSafeSegment(adwId)) return json({ error: 'invalid adw_id' }, 400);
        if (!db.session(adwId)) return notFound(`no session ${adwId}`);
        if (!req.headers.get('content-type')?.startsWith('application/json')) {
          return json({ error: 'Expected a JSON refresh request.' }, 415);
        }
        try { return json(await reportRefresher.refresh(adwId)); }
        catch (error) { return json({ error: (error as Error).message }, 502); }
      }),
    },

    "/api/health": safely(
      () =>
        json({
          ok: true,
          db: db.path,
          journal_mode: db.journalMode,
          sessions: db.sessionCount(),
        } satisfies HealthResponse),
    ),

    "/api/sessions": safely((req) => json(db.sessions(intQuery(req, "limit", 200)))),

    "/api/sessions/:adw_id": safely((req) => {
      const detail = db.sessionDetail(param(req, "adw_id"));
      return detail ? json(detail) : notFound(`no session ${param(req, "adw_id")}`);
    }),

    // The one write. Archiving is review triage — it belongs to the reader, not
    // to the run — so it never touches anything a tracer wrote.
    "/api/sessions/:adw_id/archive": {
      POST: safely(async (req) => {
        const adwId = param(req, "adw_id");
        if (!isSafeSegment(adwId)) {
          return json({ error: "invalid adw_id" } satisfies ApiError, 400);
        }
        const body = (await req.json().catch(() => ({}))) as { archived?: unknown };
        const archived = body.archived === undefined ? true : Boolean(body.archived);
        return db.setArchived(adwId, archived)
          ? json({ adw_id: adwId, archived })
          : notFound(`no session ${adwId}`);
      }),
    },

    "/api/sessions/:adw_id/events": safely((req) =>
      json(
        db.events(
          param(req, "adw_id"),
          intQuery(req, "after", 0),
          intQuery(req, "limit", 500),
        ),
      ),
    ),

    "/api/documents/view.html": {
      GET: safely((req) => {
        const query = new URL(req.url).searchParams;
        const adwId = query.get("session") ?? "";
        if (!isSafeSegment(adwId)) return json({ error: "invalid session" }, 400);
        if (!db.session(adwId)) return notFound(`no session ${adwId}`);
        return markdownResponse(FINANCE_ROOT, db.sessionsDir, query.get("path") ?? "", adwId);
      }),
    },

    "/api/sessions/:adw_id/reports/remaining-work.html": {
      GET: safely((req) => {
        const adwId = param(req, "adw_id");
        if (!isSafeSegment(adwId)) return json({ error: "invalid adw_id" }, 400);
        if (!db.session(adwId)) return notFound(`no session ${adwId}`);
        return remainingWorkResponse(db.sessionsDir, adwId);
      }),
    },

    "/api/sessions/:adw_id/reports/expenses/:expense_id/documents/:index": {
      GET: safely((req) => {
        const adwId = param(req, "adw_id");
        if (!isSafeSegment(adwId)) return json({ error: "invalid adw_id" }, 400);
        if (!db.session(adwId)) return notFound(`no session ${adwId}`);
        return expenseDocumentResponse(db.sessionsDir, FINANCE_ROOT,
          adwId, param(req, "expense_id"), param(req, "index"));
      }),
    },

    // Build HTML only for this explicit request; normal viewer polling never calls it.
    "/api/sessions/:adw_id/pending-question": {
      GET: safely(async (req) => {
        const adwId = param(req, "adw_id");
        if (!isSafeSegment(adwId)) return json({ error: "invalid adw_id" }, 400);
        const session = db.session(adwId);
        if (!session) return notFound(`no session ${adwId}`);
        const messages: string[] = [];
        let cursor = 0;
        let more = true;
        while (more) {
          const page = db.events(adwId, cursor, 1000);
          messages.push(...consoleMessages(page.events));
          cursor = page.cursor;
          more = page.has_more;
        }
        const question = pendingQuestion(messages, session.status);
        const request = pdfRequest(question);
        let checks = '';
        if (request) {
          try { checks = (await readPdf(request, FINANCE_ROOT, false)).check_image_pdf; }
          catch { /* The question still renders if document tools are unavailable. */ }
        }
        return new Response(renderQuestionPage(question, session, checks), {
          headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
            "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
            "x-content-type-options": "nosniff" },
        });
      }),
    },

    "/api/sessions/:adw_id/check-image-pdf": {
      GET: safely((req) => paymentPdf(req, false)),
    },
    "/api/sessions/:adw_id/pdf-info": {
      GET: safely((req) => paymentPdf(req, true)),
    },

    "/api/sessions/:adw_id/receipt": {
      GET: safely(async (req) => {
        const adwId = param(req, "adw_id");
        if (!isSafeSegment(adwId)) return json({ error: "invalid adw_id" }, 400);
        const session = db.session(adwId);
        if (!session) return notFound(`no session ${adwId}`);
        const messages: string[] = [];
        let cursor = 0;
        let more = true;
        while (more) {
          const page = db.events(adwId, cursor, 1000);
          messages.push(...consoleMessages(page.events));
          cursor = page.cursor;
          more = page.has_more;
        }
        const question = pendingQuestion(messages, session.status);
        const receipt = question?.sources.find(source => source.details.includes('Receipt'));
        // The path comes only from the selected question's recorded receipt, not request input.
        return receiptResponse(receipt?.path ?? '', FINANCE_ROOT);
      }),
    },

    "/api/sessions/:adw_id/pdf": {
      GET: safely(async (req) => {
        const adwId = param(req, "adw_id");
        if (!isSafeSegment(adwId)) return json({ error: "invalid adw_id" }, 400);
        const session = db.session(adwId);
        if (!session) return notFound(`no session ${adwId}`);
        const index = new URL(req.url).searchParams.get('index') ?? '0';
        if (!/^\d+$/.test(index)) return json({ error: "invalid PDF index" }, 400);
        const messages: string[] = [];
        let cursor = 0;
        let more = true;
        while (more) {
          const page = db.events(adwId, cursor, 1000);
          messages.push(...consoleMessages(page.events));
          cursor = page.cursor;
          more = page.has_more;
        }
        const question = pendingQuestion(messages, session.status);
        // Only a PDF already linked to this payment can be opened; no path input.
        const document = linkedPdfs(question)[Number(index)];
        return pdfResponse(document?.path ?? '', FINANCE_ROOT);
      }),
    },

    "/api/sessions/:adw_id/envelopes": safely((req) =>
      json(db.envelopes(param(req, "adw_id"))),
    ),

    "/api/sessions/:adw_id/gates": safely((req) => json(db.gates(param(req, "adw_id")))),

    // The exact prompts an agent was sent, read from the session dir. Files are
    // the raw record; the db has no copy of them.
    "/api/sessions/:adw_id/agents/:agent/prompts": safely(async (req) => {
      const adwId = param(req, "adw_id");
      const agent = param(req, "agent");
      if (!isSafeSegment(adwId) || !isSafeSegment(agent)) {
        return json({ error: "invalid adw_id or agent" } satisfies ApiError, 400);
      }
      if (!db.session(adwId)) return notFound(`no session ${adwId}`);

      const dir = resolve(db.sessionsDir, adwId, agent, "prompts");
      // Defense in depth: the segment check already forbids traversal.
      if (dir !== db.sessionsDir && !dir.startsWith(db.sessionsDir + sep)) {
        return json({ error: "invalid path" } satisfies ApiError, 400);
      }

      // A prompt file is absent whenever the agent never ran in this session —
      // a normal state, so it reads as null rather than an error.
      const read = async (name: string): Promise<string | null> => {
        const file = Bun.file(join(dir, `${name}.md`));
        return (await file.exists()) ? await file.text() : null;
      };
      return json({
        system: await read("system"),
        user: await read("user"),
      } satisfies AgentPrompts);
    }),
  },

  fetch(req) {
    const { pathname } = new URL(req.url);
    if (pathname.startsWith("/api/")) return notFound(`no route ${pathname}`);
    return serveStatic(req);
  },
});

console.log(`[sssf] visualizer api  http://localhost:${server.port}`);
console.log(`[sssf] db              ${db.path}  [journal_mode=${db.journalMode}]`);
console.log(`[sssf] finance sources ${FINANCE_ROOT}`);
console.log(
  existsSync(DIST_DIR)
    ? `[sssf] serving ui from  ${DIST_DIR}`
    : `[sssf] no ./dist — use "bun run dev" for the Vite dev server on :4601`,
);

process.on("SIGINT", () => {
  db.close();
  process.exit(0);
});
