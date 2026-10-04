/**
 * Typed REST client for the OpenManus web API. Every request is same-origin with the session cookie.
 */
import type {
  AgentsResponse,
  AuthConfig,
  Conversation,
  ConversationDetail,
  FileEntry,
  Health,
  Run,
  RunEvent,
  SendMessageResponse,
  Settings,
  SettingsUpdate,
  Status,
  TestLlmResult,
  User,
} from "./types";

export const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

/** Registers the app-wide reaction to a 401 (clear caches, go to /login). */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

export function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}

function extractDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
    // FastAPI validation errors: [{loc, msg, type}]
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first?.msg === "string") return first.msg;
    }
  }
  return fallback;
}

export async function parseError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // non-JSON error body
  }
  return new ApiError(response.status, extractDetail(body, response.statusText || `HTTP ${response.status}`));
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** Do not trigger the global 401 handler (used by the login form and the session probe). */
  skipAuthRedirect?: boolean;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, skipAuthRedirect = false } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: BodyInit | undefined;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: payload,
      signal,
      credentials: "same-origin",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network");
  }
  if (response.status === 401 && !skipAuthRedirect) notifyUnauthorized();
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Encodes a workspace-relative posix path segment by segment. */
export function encodePath(path: string): string {
  return path
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

export const urls = {
  file: (conversationId: string, path: string, download = false) =>
    `${API_BASE}/conversations/${encodeURIComponent(conversationId)}/files/${encodePath(path)}${download ? "?download=1" : ""}`,
  filesZip: (conversationId: string) => `${API_BASE}/conversations/${encodeURIComponent(conversationId)}/files.zip`,
  export: (conversationId: string) => `${API_BASE}/conversations/${encodeURIComponent(conversationId)}/export.md`,
  runEvents: (runId: string, after: number) => `${API_BASE}/runs/${encodeURIComponent(runId)}/events?after=${after}`,
};

const id = encodeURIComponent;

export const api = {
  health: () => apiRequest<Health>("/health", { skipAuthRedirect: true }),
  status: () => apiRequest<Status>("/status"),

  authConfig: () => apiRequest<AuthConfig>("/auth/config", { skipAuthRedirect: true }),
  me: () => apiRequest<User>("/auth/me", { skipAuthRedirect: true }),
  login: (username: string, password: string) =>
    apiRequest<User>("/auth/login", { method: "POST", body: { username, password }, skipAuthRedirect: true }),
  register: (username: string, password: string) =>
    apiRequest<User>("/auth/register", { method: "POST", body: { username, password }, skipAuthRedirect: true }),
  logout: () => apiRequest<void>("/auth/logout", { method: "POST", skipAuthRedirect: true }),
  changePassword: (current_password: string, new_password: string) =>
    apiRequest<void>("/auth/change-password", { method: "POST", body: { current_password, new_password } }),

  agents: () => apiRequest<AgentsResponse>("/agents"),

  conversations: (q?: string) =>
    apiRequest<Conversation[]>(q ? `/conversations?q=${encodeURIComponent(q)}` : "/conversations"),
  createConversation: (body: { title?: string; mode?: string }) =>
    apiRequest<Conversation>("/conversations", { method: "POST", body }),
  conversation: (conversationId: string) => apiRequest<ConversationDetail>(`/conversations/${id(conversationId)}`),
  updateConversation: (conversationId: string, body: { title?: string; mode?: string; pinned?: boolean }) =>
    apiRequest<Conversation>(`/conversations/${id(conversationId)}`, { method: "PATCH", body }),
  deleteConversation: (conversationId: string) =>
    apiRequest<void>(`/conversations/${id(conversationId)}`, { method: "DELETE" }),
  sendMessage: (conversationId: string, body: { content: string; mode?: string; attachments?: string[] }) =>
    apiRequest<SendMessageResponse>(`/conversations/${id(conversationId)}/messages`, { method: "POST", body }),
  retry: (conversationId: string) =>
    apiRequest<SendMessageResponse>(`/conversations/${id(conversationId)}/retry`, { method: "POST" }),

  run: (runId: string) => apiRequest<Run>(`/runs/${id(runId)}`),
  runEvents: (runId: string, after: number, signal?: AbortSignal) =>
    apiRequest<RunEvent[]>(`/runs/${id(runId)}/events.json?after=${after}`, { signal }),
  cancelRun: (runId: string) => apiRequest<Run>(`/runs/${id(runId)}/cancel`, { method: "POST" }),
  answer: (runId: string, question_id: string, answer: string) =>
    apiRequest<void>(`/runs/${id(runId)}/answer`, { method: "POST", body: { question_id, answer } }),

  files: (conversationId: string) => apiRequest<FileEntry[]>(`/conversations/${id(conversationId)}/files`),
  deleteFile: (conversationId: string, path: string) =>
    apiRequest<void>(`/conversations/${id(conversationId)}/files/${encodePath(path)}`, { method: "DELETE" }),
  fileText: async (conversationId: string, path: string, signal?: AbortSignal): Promise<string> => {
    const response = await fetch(urls.file(conversationId, path), { credentials: "same-origin", signal });
    if (response.status === 401) notifyUnauthorized();
    if (!response.ok) throw await parseError(response);
    return response.text();
  },

  settings: () => apiRequest<Settings>("/settings"),
  updateSettings: (body: SettingsUpdate) => apiRequest<Settings>("/settings", { method: "PUT", body }),
  testLlm: (which: "llm" | "llm_vision") =>
    apiRequest<TestLlmResult>("/settings/test-llm", { method: "POST", body: { which } }),

  users: () => apiRequest<User[]>("/users"),
  createUser: (body: { username: string; password: string; is_admin?: boolean }) =>
    apiRequest<User>("/users", { method: "POST", body }),
  updateUser: (userId: string, body: { is_admin?: boolean; disabled?: boolean; password?: string }) =>
    apiRequest<User>(`/users/${id(userId)}`, { method: "PATCH", body }),
  deleteUser: (userId: string) => apiRequest<void>(`/users/${id(userId)}`, { method: "DELETE" }),
};

export interface UploadHandle {
  promise: Promise<FileEntry[]>;
  abort: () => void;
}

/**
 * Uploads files with progress reporting (fetch has no upload progress, hence XHR).
 * `onProgress` receives a 0..1 fraction.
 */
export function uploadFiles(
  conversationId: string,
  files: File[],
  options: { dir?: string; onProgress?: (fraction: number) => void } = {},
): UploadHandle {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<FileEntry[]>((resolve, reject) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    form.append("dir", options.dir ?? "uploads");
    xhr.open("POST", `${API_BASE}/conversations/${id(conversationId)}/files`);
    xhr.withCredentials = true;
    xhr.responseType = "text";
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && options.onProgress) options.onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        // non-JSON body
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        options.onProgress?.(1);
        resolve(body as FileEntry[]);
        return;
      }
      if (xhr.status === 401) notifyUnauthorized();
      reject(new ApiError(xhr.status, extractDetail(body, xhr.statusText || `HTTP ${xhr.status}`)));
    };
    xhr.onerror = () => reject(new ApiError(0, "network"));
    xhr.onabort = () => reject(new DOMException("Upload aborted", "AbortError"));
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}
