import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, GitBranch, Clock, Upload } from "lucide-react";
import { pipelines as api } from "../api/client";
import type { Pipeline } from "../types";
import { Modal } from "../components/Modal";
import { EnvVarStatus } from "../components/EnvVarStatus";
import yaml from "js-yaml";
import Editor from "@monaco-editor/react";

const TEMPLATE_YAML = `version: "2.0"
name: ""
workspace: ./workspace
concurrency: 4
vars: {}
pipeline:
  preprocess: []
  casewise: []
  postprocess: []
`;

function fmtDate(s: string) {
  return new Date(s).toLocaleString();
}

export function PipelineList() {
  const nav = useNavigate();
  const [items, setItems] = useState<Pipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [manifest, setManifest] = useState(TEMPLATE_YAML);
  const [creating, setCreating] = useState(false);

  // import
  const [showImport, setShowImport] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<{
    name: string;
    manifest: Record<string, unknown>;
    components: string[];
  } | null>(null);
  const [importing, setImporting] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .list()
      .then(setItems)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const handleCreate = async () => {
    try {
      setCreating(true);
      const m = yaml.load(manifest) as Record<string, unknown>;
      if (name) m.name = name;
      const p = await api.create({ name: name || (m.name as string) || "untitled", manifest: m });
      setShowCreate(false);
      setName("");
      setManifest(TEMPLATE_YAML);
      nav(`/pipelines/${p.id}`);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "创建流水线失败");
    } finally {
      setCreating(false);
    }
  };

  const handleImportFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    try {
      const text = await file.text();
      const parsed = yaml.load(text) as Record<string, unknown>;
      if (!parsed || typeof parsed !== "object" || !parsed.manifest) {
        alert("无效的导出文件：缺少 manifest 字段");
        return;
      }
      setImportFile(file);
      setImportPreview({
        name: (parsed.name as string) || "imported-pipeline",
        manifest: parsed.manifest as Record<string, unknown>,
        components: Object.keys((parsed.components as Record<string, unknown>) || {}),
      });
      setShowImport(true);
    } catch {
      alert("YAML 解析失败，请检查文件格式");
    }
  };

  const handleImportConfirm = async () => {
    if (!importFile) return;
    try {
      setImporting(true);
      const p = await api.importYaml(importFile);
      setShowImport(false);
      setImportFile(null);
      setImportPreview(null);
      nav(`/pipelines/${p.id}`);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImporting(false);
    }
  };

  return (
    <>
      <div className="mb-8 flex items-center justify-between">
        <h1 className="text-2xl font-semibold font-display text-text">流水线</h1>
        <div className="flex items-center gap-2">
          <label>
            <span className="inline-flex cursor-pointer items-center gap-1.5 rounded-btn border border-border px-3.5 py-2 text-sm font-medium text-text transition-colors hover:bg-surface-hover">
              <Upload className="h-4 w-4" />
              导入
            </span>
            <input
              type="file"
              accept=".yaml,.yml"
              onChange={handleImportFileSelect}
              className="hidden"
            />
          </label>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-btn bg-brand px-3.5 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-deep"
          >
            <Plus className="h-4 w-4" />
            新建流水线
          </button>
        </div>
      </div>

      {loading && (
        <p className="text-sm text-text-secondary">加载中...</p>
      )}
      {error && (
        <p className="text-sm text-red-600">{error}</p>
      )}

      {!loading && items.length === 0 && !error && (
        <div className="flex flex-col items-center justify-center rounded-card border border-dashed border-border py-20 text-center">
          <GitBranch className="mb-4 h-12 w-12 text-text-muted" />
          <p className="text-sm text-text-secondary">暂无流水线</p>
          <button
            onClick={() => setShowCreate(true)}
            className="mt-4 cursor-pointer text-sm font-medium text-brand-light hover:text-brand"
          >
            创建第一条流水线
          </button>
        </div>
      )}

      {items.length > 0 && (
        <div className="overflow-hidden rounded-card border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-active text-left text-xs font-medium uppercase tracking-wider text-text-secondary">
                <th className="px-5 py-3.5">名称</th>
                <th className="px-5 py-3.5">ID</th>
                <th className="px-5 py-3.5">创建时间</th>
                <th className="px-5 py-3.5">更新时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((p) => (
                <tr
                  key={p.id}
                  className="cursor-pointer bg-surface-raised transition-colors hover:bg-surface-hover"
                  onClick={() => nav(`/pipelines/${p.id}`)}
                >
                  <td className="px-5 py-3.5 font-medium text-text">
                    <Link
                      to={`/pipelines/${p.id}`}
                      className="hover:text-brand-light"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {p.name}
                    </Link>
                  </td>
                  <td className="px-5 py-3.5 font-mono text-xs text-text-muted">
                    {p.id}
                  </td>
                  <td className="px-5 py-3.5 text-text-secondary">
                    <span className="inline-flex items-center gap-1">
                      <Clock className="h-3.5 w-3.5" />
                      {fmtDate(p.created_at)}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-text-secondary">
                    {fmtDate(p.updated_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        title="新建流水线"
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-text">
              名称
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-pipeline"
              className="w-full rounded-btn border border-border bg-surface-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-text">
              Manifest (YAML)
            </label>
            <Editor
              height="300px"
              language="yaml"
              theme="vs-dark"
              value={manifest}
              onChange={(val) => setManifest(val ?? "")}
              options={{
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                tabSize: 2,
              }}
            />
          </div>
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setShowCreate(false)}
              className="cursor-pointer rounded-btn border border-border px-4 py-2 text-sm font-medium text-text transition-colors hover:bg-surface-hover"
            >
              取消
            </button>
            <button
              onClick={handleCreate}
              disabled={creating}
              className="cursor-pointer rounded-btn bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-deep disabled:opacity-50"
            >
              {creating ? "创建中..." : "创建"}
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        open={showImport}
        onClose={() => {
          setShowImport(false);
          setImportFile(null);
          setImportPreview(null);
        }}
        title="导入流水线"
      >
        {importPreview && (
          <div className="space-y-4">
            <div>
              <p className="text-sm text-text">
                <span className="font-medium">名称：</span>
                {importPreview.name}
              </p>
              {importPreview.components.length > 0 && (
                <p className="mt-1 text-sm text-text-secondary">
                  包含 {importPreview.components.length} 个组件：
                  {importPreview.components.join("、")}
                </p>
              )}
            </div>

            <EnvVarStatus manifest={importPreview.manifest} />

            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setShowImport(false);
                  setImportFile(null);
                  setImportPreview(null);
                }}
                className="cursor-pointer rounded-btn border border-border px-4 py-2 text-sm font-medium text-text transition-colors hover:bg-surface-hover"
              >
                取消
              </button>
              <button
                onClick={handleImportConfirm}
                disabled={importing}
                className="cursor-pointer rounded-btn bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-deep disabled:opacity-50"
              >
                {importing ? "导入中..." : "确认导入"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
