import type { ClientOperation, RetryClass } from "./generated.js";

export const defaultTimeoutMs = 30_000;
export const maxAttempts = 3;
export const redacted = "[REDACTED]";

export interface AccessTokenProvider {
  accessToken(signal?: AbortSignal): Promise<string>;
  refreshAccessToken(signal?: AbortSignal): Promise<string>;
}

export interface RequestPlan {
  readonly operation: ClientOperation;
  readonly path: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body?: ReadableStream<Uint8Array>;
  readonly timeoutMs: number;
  readonly signal?: AbortSignal;
}

export interface TransportResponse {
  readonly status: number;
  readonly bodyStarted: boolean;
}

export interface ClientTransport {
  execute(plan: RequestPlan): Promise<TransportResponse>;
}

export function shouldRetry(input: {
  retryClass: RetryClass;
  status: number;
  attempt: number;
  bodyStarted: boolean;
}): boolean {
  if (input.retryClass === "never" || input.attempt >= maxAttempts || input.bodyStarted) {
    return false;
  }
  return new Set([408, 425, 429, 500, 502, 503, 504]).has(input.status);
}

export function redactHeaders(
  headers: Readonly<Record<string, string>>,
): Readonly<Record<string, string>> {
  const sensitive = new Set([
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-provider-token",
  ]);
  return Object.fromEntries(
    Object.entries(headers)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, value]) => [name, sensitive.has(name.toLowerCase()) ? redacted : value]),
  );
}
