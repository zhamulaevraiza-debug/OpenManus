import type { FileEntry } from "@/api/types";

export interface TreeNode {
  entry: FileEntry;
  children: TreeNode[];
}

function compareNodes(a: TreeNode, b: TreeNode): number {
  if (a.entry.is_dir !== b.entry.is_dir) return a.entry.is_dir ? -1 : 1;
  return a.entry.name.localeCompare(b.entry.name, undefined, { numeric: true, sensitivity: "base" });
}

/**
 * Builds a sorted tree (folders first) from the flat recursive listing. Parent folders missing
 * from the listing are synthesized so every file is reachable.
 */
export function buildFileTree(entries: readonly FileEntry[]): TreeNode[] {
  const nodes = new Map<string, TreeNode>();
  const roots: TreeNode[] = [];

  const ensureDir = (path: string): TreeNode => {
    const existing = nodes.get(path);
    if (existing) return existing;
    const name = path.split("/").pop() ?? path;
    const node: TreeNode = {
      entry: { path, name, is_dir: true, size: 0, modified_at: "", mime: null },
      children: [],
    };
    nodes.set(path, node);
    attach(node);
    return node;
  };

  const attach = (node: TreeNode) => {
    const slash = node.entry.path.lastIndexOf("/");
    if (slash === -1) roots.push(node);
    else ensureDir(node.entry.path.slice(0, slash)).children.push(node);
  };

  for (const entry of entries) {
    const path = entry.path.replace(/^\/+|\/+$/g, "");
    if (!path) continue;
    const existing = nodes.get(path);
    if (existing) {
      existing.entry = { ...entry, path };
      continue;
    }
    const node: TreeNode = { entry: { ...entry, path }, children: [] };
    nodes.set(path, node);
    attach(node);
  }

  const sort = (list: TreeNode[]) => {
    list.sort(compareNodes);
    list.forEach((node) => sort(node.children));
  };
  sort(roots);
  return roots;
}

/** Total size of the files in a subtree. */
export function treeSize(node: TreeNode): number {
  if (!node.entry.is_dir) return node.entry.size;
  return node.children.reduce((total, child) => total + treeSize(child), 0);
}
