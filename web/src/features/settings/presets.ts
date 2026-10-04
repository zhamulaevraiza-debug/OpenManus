/** Provider presets for the model settings form (OpenAI-compatible endpoints unless noted). */
export interface ProviderPreset {
  id: string;
  label: string;
  apiType: string;
  baseUrl: string;
  model: string;
  apiVersion?: string;
}

export const PROVIDER_PRESETS: readonly ProviderPreset[] = [
  { id: "openai", label: "OpenAI", apiType: "", baseUrl: "https://api.openai.com/v1", model: "gpt-4o" },
  {
    id: "anthropic",
    label: "Anthropic",
    apiType: "",
    baseUrl: "https://api.anthropic.com/v1/",
    model: "claude-opus-5-5",
  },
  {
    id: "gemini",
    label: "Google Gemini",
    apiType: "",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/",
    model: "gemini-2.5-pro",
  },
  {
    id: "azure",
    label: "Azure OpenAI",
    apiType: "azure",
    baseUrl: "https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT",
    model: "gpt-4o",
    apiVersion: "2024-08-01-preview",
  },
  { id: "ollama", label: "Ollama", apiType: "ollama", baseUrl: "http://localhost:11434/v1", model: "llama3.2" },
  {
    id: "openrouter",
    label: "OpenRouter",
    apiType: "",
    baseUrl: "https://openrouter.ai/api/v1",
    model: "openai/gpt-4o",
  },
  { id: "deepseek", label: "DeepSeek", apiType: "", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
];
