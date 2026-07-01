/**
 * Server-Sent Events reader for POST endpoints (SSE-from-POST).
 * Falls back to long-polling if no `stage` event is received within 5s.
 */
"use client";
import { useEffect, useRef } from "react";

export type SSEHandler = (event: string, data: string) => void;

// Mirror lib/api.ts so streaming respects the deploy topology:
// - Server-side (SSR) uses the in-container backend DNS name.
// - Browser uses a relative NEXT_PUBLIC_API_BASE ("/compliance/api") when set
// (shared platform behind nginx + basePath), else the same-origin "/api"
// proxy (standalone/local). Raw fetch() is NOT auto-prefixed by basePath.
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
export function useSSEStream(
 path: string | null,
 body: unknown,
 onEvent: SSEHandler
) {
 const onEventRef = useRef(onEvent);
 onEventRef.current = onEvent;

 useEffect(() => {
 if (!path) return;
 const ctrl = new AbortController();
 streamSSE(path, body, (e, d) => onEventRef.current(e, d), ctrl.signal).catch((err) => {
 if (ctrl.signal.aborted) return;
 onEventRef.current("error", JSON.stringify({ message: String(err) }));
 });
 return () => ctrl.abort();
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [path]);
}
