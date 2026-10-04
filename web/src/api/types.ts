/**
 * Wire types of the OpenManus web API (SPEC section 2). Timestamps are ISO-8601 UTC strings.
 */

export interface User {
  id: string;
  username: string;
  is_admin: boolean;
  disabled: boolean;
  created_at: string;
}

export interface AgentInfo {
  key: string;
  name: string;
  description: string;
  icon: string;
  available: boolean;
  reason: string | null;
  team_member: boolean;
}

export type BuiltinModeKey = "auto" | "chat" | "team";

export interface Mode {
  key: BuiltinModeKey;
}

export interface AgentsResponse {
  modes: Mode[];
  agents: AgentInfo[];
}

export interface Conversation {
  id: string;
  title: string;
  mode: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  last_message_preview: string | null;
  active_run_id: string | null;
}

export type MessageRole = "user" | "assistant";

export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  run_id: string | null;
  attachments: string[];
  created_at: string;
}

export type RunStatus = "queued" | "running" | "waiting_input" | "completed" | "failed" | "cancelled";

export interface Usage {
  input_tokens: number;
  completion_tokens: number;
}

export interface PendingQuestion {
  question_id: string;
  question: string;
}

export interface Run {
  id: string;
  conversation_id: string;
  mode: string;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  usage: Usage | null;
  pending_question: PendingQuestion | null;
  last_seq: number;
}

export interface RunEvent {
  seq: number;
  run_id: string;
  type: string;
  ts: string;
  data: Record<string, unknown>;
}

export interface FileEntry {
  path: string;
  name: string;
  is_dir: boolean;
  size: number;
  modified_at: string;
  mime: string | null;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: Message[];
  runs: Run[];
}

export interface SendMessageResponse {
  message: Message;
  run: Run;
}

export type LlmApiType = "" | "openai" | "azure" | "aws" | "ollama";

export interface LLMSettingsView {
  api_type: string;
  model: string;
  base_url: string;
  api_key_set: boolean;
  api_key_hint: string | null;
  max_tokens: number;
  temperature: number;
  api_version: string;
  supports_images: boolean | null;
}

export interface McpServerConfig {
  type: "sse" | "stdio";
  url?: string;
  command?: string;
  args?: string[];
}

export interface SearchSettings {
  engine: string;
  fallback_engines: string[];
  lang: string;
  country: string;
}

export interface Settings {
  llm: LLMSettingsView;
  llm_vision: LLMSettingsView | null;
  search: SearchSettings;
  browser: { headless: boolean };
  team: { max_plan_steps: number };
  runtime: { max_steps: number };
  mcp_servers: Record<string, McpServerConfig>;
  allow_registration: boolean;
  locked_by_env: string[];
}

/** Writable LLM fields; `api_key` is write-only (absent/empty keeps the stored key). */
export interface LLMSettingsUpdate {
  api_type?: string;
  model?: string;
  base_url?: string;
  api_key?: string;
  max_tokens?: number;
  temperature?: number;
  api_version?: string;
  supports_images?: boolean | null;
}

export interface SettingsUpdate {
  llm?: LLMSettingsUpdate;
  llm_vision?: LLMSettingsUpdate | null;
  search?: Partial<SearchSettings>;
  browser?: Partial<Settings["browser"]>;
  team?: Partial<Settings["team"]>;
  runtime?: Partial<Settings["runtime"]>;
  mcp_servers?: Record<string, McpServerConfig>;
  allow_registration?: boolean;
}

export interface TestLlmResult {
  ok: boolean;
  message: string;
  latency_ms: number;
}

export interface Status {
  version: string;
  llm_configured: boolean;
  active_runs: number;
  max_concurrent_runs: number;
  is_admin: boolean;
}

export interface Health {
  status: string;
  version: string;
}

export interface AuthConfig {
  allow_registration: boolean;
}
