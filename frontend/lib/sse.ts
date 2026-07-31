/**
 * Server-Sent Events reader for POST endpoints (SSE-from-POST).
 * Falls back to long-polling if no `stage` event is received within 5s.
 */
"use client";
import { useEffect, useRef } from "react";

export type SSEHandler = (event: string, data: string) => void;

// Mirror lib/api.ts so streaming respects the deploy topology:
//  - Server-side (SSR) uses the in-container backend DNS name.
//  - Browser uses a relative NEXT_PUBLIC_API_BASE ("/compliance/api") when set
//    (shared platform behind nginx + basePath), else the same-origin "/api"
//    proxy (standalone/local). Raw fetch() is NOT auto-prefixed by basePath.
const SERVER_BASE =
  process.env.INTERNAL_API_BASE ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://localhost:8000";
const PUBLIC_API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
const BROWSER_BASE = PUBLIC_API_BASE.startsWith("/") ? PUBLIC_API_BASE : "/api";
const sseBase = () => (typeof window === "undefined" ? SERVER_BASE : BROWSER_BASE);

export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: SSEHandler,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${sseBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`SSE failed: ${res.status} ${res.statusText}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseSSEEvent(raw);
      if (ev) onEvent(ev.event, ev.data);
    }
  }
}

function parseSSEEvent(raw: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) {
      // SSE spec: strip exactly ONE leading space if present. .trimStart()
      // ate token-leading spaces and mashed streamed text together.
      let value = line.slice(5);
      if (value.startsWith(" ")) value = value.slice(1);
      dataLines.push(value);
    }
  }
  if (!dataLines.length) return null;
  return { event, data: dataLines.join("\n") };
}

/**
 * React hook: runs an SSE stream with auto-abort on unmount.
 */
/**
 * One live stream per path, shared by every subscriber.
 *
 * Why this exists: aborting the fetch does NOT stop the work. The analyze
 * endpoint spawns the run and then deliberately lets it finish even if the
 * client goes away ("Let it finish in background — don't cancel mid-flight",
 * compliance.py). So every POST that reaches the server costs a full analysis,
 * whether or not the browser still cares about the response.
 *
 * A second POST therefore is not a wasted request, it is a second graded run:
 * ~100 LLM calls, competing for the same token quota as the first. Production
 * logs on 2026-07-31 showed two stream POSTs ~600ms apart and two complete runs
 * for one document. Anything that remounts the component — StrictMode's
 * double-invoke, a router.refresh(), a parent re-render — used to buy another
 * one.
 *
 * Subscribers attach to the existing stream instead of opening their own, and
 * teardown is deferred briefly so an immediate remount reattaches to the live
 * stream rather than racing its abort.
 */
type StreamEntry = {
  ctrl: AbortController;
  subscribers: Set<SSEHandler>;
  closeTimer: ReturnType<typeof setTimeout> | null;
};

const _streams = new Map<string, StreamEntry>();

// Long enough to span a synchronous unmount/remount, short enough that a real
// navigation away still tears the stream down promptly.
const _TEARDOWN_GRACE_MS = 250;

function _subscribe(path: string, body: unknown, handler: SSEHandler): () => void {
  let entry = _streams.get(path);

  if (entry) {
    // A stream is already live (or pending teardown) for this path — join it.
    if (entry.closeTimer !== null) {
      clearTimeout(entry.closeTimer);
      entry.closeTimer = null;
    }
    entry.subscribers.add(handler);
  } else {
    const ctrl = new AbortController();
    const created: StreamEntry = { ctrl, subscribers: new Set([handler]), closeTimer: null };
    _streams.set(path, created);
    entry = created;

    const fanout: SSEHandler = (e, d) => {
      for (const sub of Array.from(created.subscribers)) sub(e, d);
    };

    streamSSE(path, body, fanout, ctrl.signal)
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        fanout("error", JSON.stringify({ message: String(err) }));
      })
      .finally(() => {
        // Only clear if this entry is still the current one for the path;
        // a later subscriber may already have started a fresh stream.
        if (_streams.get(path) === created) _streams.delete(path);
      });
  }

  const joined = entry;
  return () => {
    joined.subscribers.delete(handler);
    if (joined.subscribers.size > 0 || joined.closeTimer !== null) return;
    joined.closeTimer = setTimeout(() => {
      if (joined.subscribers.size === 0) {
        joined.ctrl.abort();
        if (_streams.get(path) === joined) _streams.delete(path);
      }
    }, _TEARDOWN_GRACE_MS);
  };
}

export function useSSEStream(
  path: string | null,
  body: unknown,
  onEvent: SSEHandler
) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!path) return;
    // Stable identity so the registry can add/remove exactly this subscriber
    // while still calling the latest handler.
    const handler: SSEHandler = (e, d) => onEventRef.current(e, d);
    return _subscribe(path, body, handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
}
