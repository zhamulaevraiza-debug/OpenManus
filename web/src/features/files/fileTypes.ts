/** File classification for icons and previews. */
import {
  File,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Folder,
  type LucideIcon,
} from "lucide-react";

export type PreviewKind = "image" | "svg" | "html" | "pdf" | "markdown" | "code" | "text" | "audio" | "video" | "none";

const CODE_LANGUAGES: Record<string, string> = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  json: "json",
  css: "css",
  scss: "scss",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  yml: "yaml",
  yaml: "yaml",
  toml: "ini",
  ini: "ini",
  xml: "xml",
  sql: "sql",
  go: "go",
  rs: "rust",
  java: "java",
  kt: "kotlin",
  c: "c",
  h: "c",
  cpp: "cpp",
  hpp: "cpp",
  cs: "csharp",
  rb: "ruby",
  php: "php",
  swift: "swift",
  r: "r",
  lua: "lua",
  pl: "perl",
  dockerfile: "dockerfile",
  makefile: "makefile",
  diff: "diff",
};

const TEXT_EXTENSIONS = new Set(["txt", "log", "csv", "tsv", "env", "cfg", "conf", "rst", "gitignore", "lock"]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "avif", "ico"]);
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "ogg", "m4a", "flac", "aac", "oga"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "mov", "m4v", "ogv"]);
const ARCHIVE_EXTENSIONS = new Set(["zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"]);
const SHEET_EXTENSIONS = new Set(["csv", "tsv", "xls", "xlsx", "ods"]);

export function extension(name: string): string {
  const lower = name.toLowerCase();
  if (lower === "dockerfile" || lower === "makefile") return lower;
  const dot = lower.lastIndexOf(".");
  return dot > 0 ? lower.slice(dot + 1) : "";
}

export function previewKind(name: string, mime: string | null): PreviewKind {
  const ext = extension(name);
  const type = mime ?? "";
  if (ext === "svg" || type === "image/svg+xml") return "svg";
  if (ext === "html" || ext === "htm" || type === "text/html") return "html";
  if (IMAGE_EXTENSIONS.has(ext) || type.startsWith("image/")) return "image";
  if (ext === "pdf" || type === "application/pdf") return "pdf";
  if (ext === "md" || ext === "markdown" || type === "text/markdown") return "markdown";
  if (AUDIO_EXTENSIONS.has(ext) || type.startsWith("audio/")) return "audio";
  if (VIDEO_EXTENSIONS.has(ext) || type.startsWith("video/")) return "video";
  if (CODE_LANGUAGES[ext]) return "code";
  if (TEXT_EXTENSIONS.has(ext) || type.startsWith("text/") || type === "application/json") return "text";
  return "none";
}

export function codeLanguage(name: string): string {
  return CODE_LANGUAGES[extension(name)] ?? "plaintext";
}

export function fileIcon(name: string, isDir: boolean, mime: string | null = null): LucideIcon {
  if (isDir) return Folder;
  const ext = extension(name);
  const kind = previewKind(name, mime);
  if (kind === "image" || kind === "svg") return FileImage;
  if (kind === "audio") return FileAudio;
  if (kind === "video") return FileVideo;
  if (SHEET_EXTENSIONS.has(ext)) return FileSpreadsheet;
  if (ARCHIVE_EXTENSIONS.has(ext)) return FileArchive;
  if (kind === "code" || kind === "html") return FileCode;
  if (kind === "markdown" || kind === "text" || kind === "pdf" || ext === "docx" || ext === "doc") return FileText;
  return File;
}
