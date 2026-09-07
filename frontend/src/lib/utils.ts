import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const API_BASE = "/api/v1";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || res.statusText);
  }
  return res.json() as Promise<T>;
}

/**
 * POST a JSON body and consume the Server-Sent Events response.
 *
 * EventSource is GET-only, so the SSE framing is parsed by hand: frames are
 * separated by a blank line and each carries one JSON object on `data:` lines.
 */
export async function* apiStream<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal
): AsyncGenerator<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  if (!res.body) throw new Error("Streaming is not supported by this browser");

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += value;

      let split: number;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("");
        if (data) yield JSON.parse(data) as T;
      }
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}
