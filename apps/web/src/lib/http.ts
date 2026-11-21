import type { ProblemDetails } from "./api-types";
import { isRecord } from "./guards";

/** An HTTP error response, carrying the RFC 9457 problem details when the API sent them. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly problem: ProblemDetails | null;
  /** Seconds from the `Retry-After` header (429 and 503), if present. */
  readonly retryAfterSeconds: number | null;

  constructor(status: number, problem: ProblemDetails | null, retryAfterSeconds: number | null = null) {
    super(problem?.detail ?? problem?.title ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = problem?.code ?? null;
    this.problem = problem;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export function isApiError(error: unknown, status?: number, code?: string): error is ApiError {
  if (!(error instanceof ApiError)) return false;
  if (status !== undefined && error.status !== status) return false;
  if (code !== undefined && error.code !== code) return false;
  return true;
}

/** True for failures where the request never got an HTTP response. */
export function isNetworkError(error: unknown): boolean {
  return error instanceof TypeError;
}

function parseProblem(body: unknown): ProblemDetails | null {
  if (!isRecord(body)) return null;
  const text = (key: string) => (typeof body[key] === "string" ? body[key] : undefined);
  const problem: ProblemDetails = {};
  const type = text("type");
  const title = text("title");
  const detail = text("detail");
  const code = text("code");
  if (type !== undefined) problem.type = type;
  if (title !== undefined) problem.title = title;
  if (detail !== undefined) problem.detail = detail;
  if (code !== undefined) problem.code = code;
  if (typeof body.status === "number") problem.status = body.status;
  return problem;
}

function parseRetryAfter(response: Response): number | null {
  const header = response.headers.get("Retry-After");
  if (header === null) return null;
  const seconds = Number(header);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

/** Builds an `ApiError` from a non-2xx response, reading problem+json when present. */
export async function toApiError(response: Response): Promise<ApiError> {
  let problem: ProblemDetails | null = null;
  const contentType = response.headers.get("Content-Type") ?? "";
  if (contentType.includes("json")) {
    try {
      problem = parseProblem(await response.json());
    } catch {
      problem = null;
    }
  }
  return new ApiError(response.status, problem, parseRetryAfter(response));
}

export type QueryParams = Record<string, string | number | boolean | null | undefined>;

/** Appends the defined, non-empty query parameters to a same-origin path. */
export function withQuery(path: string, query?: QueryParams): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const search = params.toString();
  return search ? `${path}?${search}` : path;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: QueryParams;
  token?: string | null;
  signal?: AbortSignal;
  /** Called with every response, success or not, before the body is read. */
  onResponse?: (response: Response) => void;
}

/**
 * Sends a JSON request to the API (same origin; nginx or the Vite proxy forwards it) and returns
 * the parsed body. Non-2xx responses throw `ApiError`; network failures throw the fetch `TypeError`.
 *
 * Bodies are not validated at runtime: the API's OpenAPI contract is the source of truth.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, token, signal, onResponse } = options;
  const headers = new Headers({ Accept: "application/json" });
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const init: RequestInit = { method, headers };
  if (body !== undefined) init.body = JSON.stringify(body);
  if (signal) init.signal = signal;

  const response = await fetch(withQuery(path, query), init);
  onResponse?.(response);
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;

  const contentType = response.headers.get("Content-Type") ?? "";
  if (!contentType.includes("json")) return undefined as T;
  const data: unknown = await response.json();
  return data as T;
}
