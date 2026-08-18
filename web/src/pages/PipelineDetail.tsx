import { useEffect, useState, useCallback, useMemo } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  ChevronLeft,
  Play,
  Trash2,
  Save,
  Upload,
  FileCode,
  X,
  Clock,
  Check,
  Download,
} from "lucide-react";
import yaml from "js-yaml";
import Editor from "@monaco-editor/react";
import {
  pipelines as pApi,
  pipelineComponents as cApi,
  runs as rApi,
  utils,
} from "../api/client";
import type { Pipeline, RunResponse, ManifestData, CustomComponentInfo } from "../types";
import { RunStatusBadge } from "../components/StatusBadge";
import { Modal } from "../components/Modal";
import { ManifestFormEditor } from "../components/ManifestFormEditor";

type Tab = "manifest" | "components" | "runs";

// ── 运行覆盖配置提取 ──

interface ConfigEntry {
  path: string;   // "global.concurrency" | "casewise.1.ai_endpoint"
  group: string;  // 分组标题
  key: string;    // 配置键名
  value: string;  // 当前值（字符串化）
}

const STAGE_LABEL: Record<string, string> = {
  preprocess: "预处理",
  casewise: "逐案例",
  postprocess: "后处理",
};

function configValueToString(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function extractConfigEntries(m: ManifestData): ConfigEntry[] {
  const entries: ConfigEntry[] = [];

  // 根级参数
  if (m.concurrency != null) {
    entries.push({ path: "concurrency", group: "基础配置", key: "concurrency", value: configValueToString(m.concurrency) });
  }
  for (const [k, v] of Object.entries(m.vars ?? {})) {
    entries.push({ path: `vars.${k}`, group: "变量 (vars)", key: k, value: configValueToString(v) });
  }

  for (const stage of ["preprocess", "casewise", "postprocess"] as const) {
    (m.pipeline?.[stage] ?? []).forEach((step, idx) => {
      if (!step.config || Object.keys(step.config).length === 0) return;
      const group = `${STAGE_LABEL[stage]} #${idx + 1} — ${step.src}`;
      for (const [k, v] of Object.entries(step.config)) {
        entries.push({ path: `${stage}.${idx}.${k}`, group, key: k, value: configValueToString(v) });
      }
    });
  }

  return entries;
}

function buildOverrides(edited: Record<string, string>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [path, raw] of Object.entries(edited)) {
    let val: unknown;
    try { val = JSON.parse(raw); } catch { val = raw; }
    const parts = path.split(".");
    const head = parts[0]!;
    const second = parts[1];
    const third = parts[2];
    if (head === "concurrency") {
      result.concurrency = val;
    } else if (head === "vars" && second) {
      const v = (result.vars ??= {}) as Record<string, unknown>;
      v[second] = val;
    } else if (second && third) {
      const s = (result[head] ??= {}) as Record<string, Record<string, unknown>>;
      const step = (s[second] ??= {});
      step[third] = val;
    }
  }
  return result;
}

function fmtDate(s: string | null) {
  return s ? new Date(s).toLocaleString() : "-";
}

export function PipelineDetail() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();

  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("manifest");

  // manifest
  const [manifestData, setManifestData] = useState<ManifestData | null>(null);
  const [yamlText, setYamlText] = useState("");
  const [mode, setMode] = useState<"form" | "yaml">("form");
  const [manifestDirty, setManifestDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [validation, setValidation] = useState<{
    valid: boolean;
    errors: string[];
  } | null>(null);

  // components
  const [compFiles, setCompFiles] = useState<CustomComponentInfo[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [fileDirty, setFileDirty] = useState(false);
  const [newCompName, setNewCompName] = useState("");

  // runs
  const [pipelineRuns, setPipelineRuns] = useState<RunResponse[]>([]);

  // run modal
  const [showRunModal, setShowRunModal] = useState(false);
  const [editedOverrides, setEditedOverrides] = useState<Record<string, string>>({});
  const [starting, setStarting] = useState(false);

  const configEntries = useMemo(
    () => (manifestData ? extractConfigEntries(manifestData) : []),
    [manifestData],
  );
  const configGroups = useMemo(() => {
    const groups: [string, ConfigEntry[]][] = [];
    for (const e of configEntries) {
      const last = groups[groups.length - 1];
      if (!last || last[0] !== e.group) {
        groups.push([e.group, [e]]);
      } else {
        last[1].push(e);
      }
    }
    return groups;
  }, [configEntries]);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const p = await pApi.get(id);
      setPipeline(p);
      const md = p.manifest as unknown as ManifestData;
      setManifestData(md);
      setYamlText(yaml.dump(md, { noRefs: true }));
      setManifestDirty(false);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "加载流水线失败");
      nav("/");
    } finally {
      setLoading(false);
    }
  }, [id, nav]);

  const switchMode = (target: "form" | "yaml") => {
    if (target === mode) return;
    if (target === "yaml" && manifestData) {
      setYamlText(yaml.dump(manifestData, { noRefs: true }));
    } else if (target === "form") {
      try {
        const parsed = yaml.load(yamlText) as ManifestData;
        setManifestData(parsed);
      } catch {
        alert("YAML 格式无效，无法切换到表单模式");
        return;
      }
    }
    setMode(target);
  };

  const loadComps = useCallback(async () => {
    if (!id) return;
    try {
      setCompFiles(await cApi.list(id));
    } catch {
      /* empty */
    }
  }, [id]);

  const loadRuns = useCallback(async () => {
    if (!id) return;
    try {
      setPipelineRuns(await rApi.list(id));
    } catch {
      /* empty */
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (tab === "components") void loadComps();
    if (tab === "runs") void loadRuns();
  }, [tab, loadComps, loadRuns]);

  const handleSaveManifest = async () => {
    if (!id) return;
    try {
      setSaving(true);
      let m: Record<string, unknown>;
      if (mode === "form") {
        m = manifestData as unknown as Record<string, unknown>;
      } else {
        m = yaml.load(yamlText) as Record<string, unknown>;
      }
      const manifestName = (m as Record<string, unknown>).name as string | undefined;
      const p = await pApi.update(id, { name: manifestName, manifest: m });
      setPipeline(p);
      const md = p.manifest as unknown as ManifestData;
      setManifestData(md);
      setYamlText(yaml.dump(md, { noRefs: true }));
      setManifestDirty(false);
      setValidation(null);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleValidate = async () => {
    try {
      let m: Record<string, unknown>;
      if (mode === "form") {
        m = manifestData as unknown as Record<string, unknown>;
      } else {
        m = yaml.load(yamlText) as Record<string, unknown>;
      }
      const res = await utils.validate(m);
      setValidation(res);
    } catch (e: unknown) {
      setValidation({
        valid: false,
        errors: [e instanceof Error ? e.message : "格式无效"],
      });
    }
  };

  const handleExport = async () => {
    if (!id) return;
    try {
      const text = await pApi.exportYaml(id);
      const blob = new Blob([text], { type: "text/yaml" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${pipeline?.name || id}.yaml`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "导出失败");
    }
  };

  const handleDelete = async () => {
    if (!id) return;
    if (!confirm("确定删除此流水线？")) return;
    await pApi.delete(id);
    nav("/");
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!id || !e.target.files?.[0]) return;
    await cApi.upload(id, e.target.files[0]);
    e.target.value = "";
    await loadComps();
  };

  const openFile = async (filename: string) => {
    if (!id) return;
    const c = await cApi.get(id, filename);
    setSelectedFile(filename);
    setFileContent(c.content);
    setFileDirty(false);
  };

  const saveFile = async () => {
    if (!id || !selectedFile) return;
    await cApi.update(id, selectedFile, fileContent);
    setFileDirty(false);
  };

  const deleteFile = async (filename: string) => {
    if (!id) return;
    if (!confirm(`确定删除 ${filename}？`)) return;
    await cApi.delete(id, filename);
    if (selectedFile === filename) {
      setSelectedFile(null);
      setFileContent("");
    }
    await loadComps();
  };

  const handleCreateComp = async () => {
    if (!id || !newCompName) return;
    const filename = newCompName.endsWith(".py") ? newCompName : `${newCompName}.py`;
    const className = filename
      .replace(/\.py$/, "")
      .split("_")
      .map((w: string) => w.charAt(0).toUpperCase() + w.slice(1))
      .join("");
    const template = `from pydantic import BaseModel, Field
from deepflow import CasewiseComponent, CasewiseOutput, CaseContext


class ${className}(CasewiseComponent):
    """TODO: 组件描述"""

    class Config(BaseModel):
        pass

    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        return CasewiseOutput(message="done")
`;
    await cApi.update(id, filename, template);
    setNewCompName("");
    await loadComps();
    await openFile(filename);
  };

  const handleRun = async () => {
    if (!id) return;
    try {
      setStarting(true);
      const o = buildOverrides(editedOverrides);
      const run = await rApi.create(
        id,
        Object.keys(o).length > 0 ? o : undefined,
      );
      setShowRunModal(false);
      nav(`/runs/${run.id}`);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "启动运行失败");
    } finally {
      setStarting(false);
    }
  };

  if (loading) return <p className="text-sm text-text-secondary">加载中...</p>;
  if (!pipeline) return null;

  const tabs: { key: Tab; label: string }[] = [
    { key: "manifest", label: "Manifest" },
    { key: "components", label: "组件" },
    { key: "runs", label: "运行记录" },
  ];

  return (
    <>
      {/* Header */}
      <div className="mb-8">
        <Link
          to="/"
          className="mb-4 inline-flex items-center gap-1 text-sm text-text-secondary hover:text-text"
        >
          <ChevronLeft className="h-4 w-4" />
          流水线
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold font-display text-text">
              {pipeline.name}
            </h1>
            <p className="mt-1 font-mono text-xs text-text-muted">
              {pipeline.id}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => { setEditedOverrides({}); setShowRunModal(true); }}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-btn bg-brand px-3.5 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-deep"
            >
              <Play className="h-4 w-4" />
              运行
            </button>
            <button
              onClick={handleExport}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-btn border border-border px-3.5 py-2 text-sm font-medium text-text transition-colors hover:bg-surface-hover"
            >
              <Download className="h-4 w-4" />
              导出
            </button>
            <button
              onClick={handleDelete}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-btn border border-border px-3.5 py-2 text-sm font-medium text-text transition-colors hover:bg-status-error hover:text-status-error-text hover:border-border"
            >
              <Trash2 className="h-4 w-4" />
              删除
            </button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="mb-6 flex gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`cursor-pointer border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
              tab === t.key
                ? "border-brand bg-brand-50 text-brand-light"
                : "border-transparent text-text-muted hover:text-text"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Manifest Tab */}
      {tab === "manifest" && (
        <div>
          <div className="mb-3 flex items-center gap-2">
            {/* Mode toggle */}
            <div className="flex rounded-pill border border-border">
              <button
                onClick={() => switchMode("form")}
                className={`cursor-pointer px-3 py-1.5 text-xs font-medium transition-colors ${
                  mode === "form"
                    ? "bg-brand-50 text-brand-light"
                    : "text-text-muted hover:text-text"
                }`}
              >
                表单
              </button>
              <button
                onClick={() => switchMode("yaml")}
                className={`cursor-pointer border-l border-border px-3 py-1.5 text-xs font-medium transition-colors ${
                  mode === "yaml"
                    ? "bg-brand-50 text-brand-light"
                    : "text-text-muted hover:text-text"
                }`}
              >
                YAML
              </button>
            </div>
            <div className="flex-1" />
            <button
              onClick={handleValidate}
              className="cursor-pointer rounded-btn border border-border px-3 py-1.5 text-xs font-medium text-text transition-colors hover:bg-surface-hover"
            >
              <Check className="mr-1 inline h-3.5 w-3.5" />
              校验
            </button>
            <button
              onClick={handleSaveManifest}
              disabled={!manifestDirty || saving}
              className="cursor-pointer rounded-btn bg-brand px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-deep disabled:opacity-40"
            >
              <Save className="mr-1 inline h-3.5 w-3.5" />
              {saving ? "保存中..." : "保存"}
            </button>
          </div>
          {validation && (
            <div
              className={`mb-4 rounded-btn border px-4 py-3 text-sm ${
                validation.valid
                  ? "border-status-success-text/20 bg-status-success text-status-success-text"
                  : "border-status-error-text/20 bg-status-error text-status-error-text"
              }`}
            >
              {validation.valid
                ? "校验通过"
                : validation.errors.map((e, i) => <p key={i}>{e}</p>)}
            </div>
          )}
          {mode === "form" && manifestData && (
            <ManifestFormEditor
              manifest={manifestData}
              pipelineId={id!}
              onChange={(m) => {
                setManifestData(m);
                setManifestDirty(true);
                setValidation(null);
              }}
            />
          )}
          {mode === "yaml" && (
            <Editor
              height="600px"
              language="yaml"
              theme="vs-dark"
              value={yamlText}
              onChange={(val) => {
                setYamlText(val ?? "");
                setManifestDirty(true);
                setValidation(null);
              }}
              options={{
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                tabSize: 2,
              }}
            />
          )}
        </div>
      )}

      {/* Components Tab */}
      {tab === "components" && (
        <div className="flex gap-4">
          <div className="w-56 flex-shrink-0">
            <div className="mb-3 flex items-center gap-2">
              <label>
                <span className="inline-flex cursor-pointer items-center gap-1.5 rounded-btn border border-border px-3 py-1.5 text-xs font-medium text-text transition-colors hover:bg-surface-hover">
                  <Upload className="h-3.5 w-3.5" />
                  上传 .py
                </span>
                <input
                  type="file"
                  accept=".py"
                  onChange={handleUpload}
                  className="hidden"
                />
              </label>
            </div>
            <div className="space-y-1">
              {compFiles.length === 0 && (
                <p className="text-xs text-text-muted">暂无组件</p>
              )}
              {compFiles.map((c) => (
                <div
                  key={c.filename}
                  className={`group flex items-center justify-between rounded-btn px-2.5 py-1.5 text-sm cursor-pointer ${
                    selectedFile === c.filename
                      ? "bg-surface-active text-text"
                      : "text-text-secondary hover:bg-surface-hover hover:text-text"
                  }`}
                  onClick={() => openFile(c.filename)}
                >
                  <span className="flex items-center gap-1.5 truncate">
                    <FileCode className="h-3.5 w-3.5 flex-shrink-0" />
                    {c.filename}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteFile(c.filename);
                    }}
                    className="cursor-pointer opacity-0 group-hover:opacity-100"
                  >
                    <X className="h-3.5 w-3.5 text-text-muted hover:text-red-500" />
                  </button>
                </div>
              ))}
            </div>
            {/* New component input */}
            <div className="mt-2">
              <input
                value={newCompName}
                onChange={(e) => setNewCompName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreateComp();
                }}
                placeholder="new_component.py"
                className="w-full rounded border border-border bg-surface-input px-2 py-1 text-xs placeholder:text-text-muted focus:border-brand focus:outline-none"
              />
            </div>
          </div>
          <div className="flex-1">
            {selectedFile ? (
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="font-mono text-sm text-text">
                    {selectedFile}
                  </span>
                  <button
                    onClick={saveFile}
                    disabled={!fileDirty}
                    className="cursor-pointer rounded-btn bg-brand px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-deep disabled:opacity-40"
                  >
                    <Save className="mr-1 inline h-3.5 w-3.5" />
                    保存
                  </button>
                </div>
                <Editor
                  height="600px"
                  language="python"
                  theme="vs-dark"
                  value={fileContent}
                  onChange={(val) => {
                    setFileContent(val ?? "");
                    setFileDirty(true);
                  }}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    lineNumbers: "on",
                    scrollBeyondLastLine: false,
                    wordWrap: "on",
                    tabSize: 4,
                  }}
                />
              </div>
            ) : (
              <div className="flex h-96 items-center justify-center text-sm text-text-muted">
                选择组件文件以查看
              </div>
            )}
          </div>
        </div>
      )}

      {/* Runs Tab */}
      {tab === "runs" && (
        <div>
          {pipelineRuns.length === 0 ? (
            <p className="py-8 text-center text-sm text-text-muted">
              暂无运行记录
            </p>
          ) : (
            <div className="overflow-hidden rounded-card border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-surface-active text-left text-xs font-medium uppercase tracking-wider text-text-secondary">
                    <th className="px-5 py-3.5">运行 ID</th>
                    <th className="px-5 py-3.5">状态</th>
                    <th className="px-5 py-3.5">开始时间</th>
                    <th className="px-5 py-3.5">完成时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {pipelineRuns.map((r) => (
                    <tr
                      key={r.id}
                      className="cursor-pointer bg-surface-raised transition-colors hover:bg-surface-hover"
                      onClick={() => nav(`/runs/${r.id}`)}
                    >
                      <td className="px-5 py-3.5 font-mono text-xs text-text-secondary">
                        {r.id}
                      </td>
                      <td className="px-5 py-3.5">
                        <RunStatusBadge status={r.status} />
                      </td>
                      <td className="px-5 py-3.5 text-text-secondary">
                        <span className="inline-flex items-center gap-1">
                          <Clock className="h-3.5 w-3.5" />
                          {fmtDate(r.started_at)}
                        </span>
                      </td>
                      <td className="px-5 py-3.5 text-text-secondary">
                        {fmtDate(r.completed_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Run Modal */}
      <Modal
        open={showRunModal}
        onClose={() => setShowRunModal(false)}
        title="运行流水线"
      >
        <div className="space-y-4">
          {configGroups.length > 0 ? (
            <div className="max-h-[60vh] space-y-3 overflow-y-auto">
              <p className="text-xs text-text-secondary">
                勾选需要覆盖的配置项，修改后的值将在本次运行中生效
              </p>
              {configGroups.map(([group, entries]) => (
                <div key={group}>
                  <p className="mb-1 text-xs font-medium text-text-secondary">{group}</p>
                  <div className="space-y-1 rounded-btn border border-border bg-surface-active p-2">
                    {entries.map((entry) => {
                      const isEdited = entry.path in editedOverrides;
                      return (
                        <label
                          key={entry.path}
                          className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-surface-hover"
                        >
                          <input
                            type="checkbox"
                            checked={isEdited}
                            onChange={(e) => {
                              setEditedOverrides((prev) => {
                                if (e.target.checked) return { ...prev, [entry.path]: entry.value };
                                const next = { ...prev };
                                delete next[entry.path];
                                return next;
                              });
                            }}
                            className="accent-blue-600"
                          />
                          <span
                            className="w-36 flex-shrink-0 truncate font-mono text-xs text-text-secondary"
                            title={entry.key}
                          >
                            {entry.key}
                          </span>
                          {isEdited ? (
                            <input
                              value={editedOverrides[entry.path]}
                              onChange={(e) =>
                                setEditedOverrides((prev) => ({ ...prev, [entry.path]: e.target.value }))
                              }
                              onClick={(e) => e.stopPropagation()}
                              className="flex-1 rounded border border-brand bg-surface-input px-2 py-0.5 font-mono text-xs text-text focus:border-brand focus:outline-none"
                            />
                          ) : (
                            <span
                              className="flex-1 truncate font-mono text-xs text-text-muted"
                              title={entry.value}
                            >
                              {entry.value || <span className="italic">（空）</span>}
                            </span>
                          )}
                        </label>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-4 text-center text-sm text-text-muted">
              此流水线无可覆盖的配置项
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setShowRunModal(false)}
              className="cursor-pointer rounded-btn border border-border px-4 py-2 text-sm font-medium text-text transition-colors hover:bg-surface-hover"
            >
              取消
            </button>
            <button
              onClick={handleRun}
              disabled={starting}
              className="cursor-pointer rounded-btn bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-deep disabled:opacity-50"
            >
              {starting ? "启动中..." : "开始运行"}
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}
