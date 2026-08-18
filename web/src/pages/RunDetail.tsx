import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { ChevronLeft, Ban, Trash2, Clock, Wifi, WifiOff } from "lucide-react";
import { runs as rApi } from "../api/client";
import type { RunResponse, MetricsResponse, CaseMetrics } from "../types";
import { RunStatusBadge } from "../components/StatusBadge";
import { useRunEvents } from "../hooks/useWebSocket";

type Tab = "events" | "metrics" | "logs";

function fmtDate(s: string | null) {
  return s ? new Date(s).toLocaleString() : "-";
}

function fmtDuration(ms: number | null) {
  if (ms == null) return "-";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 事件类型 → badge 颜色 */
function eventBadgeClass(type: string): string {
  if (type.includes("failed")) return "bg-status-error text-status-error-text";
  if (type.includes("completed")) return "bg-status-success text-status-success-text";
  if (type.includes("started")) return "bg-brand-50 text-brand-light";
  if (type.includes("cancelled")) return "bg-status-warning text-status-warning-text";
  return "bg-surface-active text-text-secondary";
}

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();

  const [run, setRun] = useState<RunResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("events");

  // events
  const [eventCaseFilter, setEventCaseFilter] = useState("");

  // metrics
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);

  // logs
  const [logs, setLogs] = useState("");

  const isActive = run?.status === "pending" || run?.status === "running";
  const { events, connected } = useRunEvents(id, isActive ?? false);

  const eventsEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  // 从事件中提取唯一 case_id
  const eventCaseIds = useMemo(
    () =>
      [...new Set(
        events
          .map((ev) => ev.data.case_id as string | undefined)
          .filter((cid): cid is string => !!cid),
      )].sort(),
    [events],
  );

  // 按 case_id 过滤事件（保留无 case_id 的全局事件）
  const filteredEvents = useMemo(
    () =>
      eventCaseFilter
        ? events.filter(
            (ev) => !ev.data.case_id || ev.data.case_id === eventCaseFilter,
          )
        : events,
    [events, eventCaseFilter],
  );

  const loadRun = useCallback(async () => {
    if (!id) return;
    try {
      const r = await rApi.get(id);
      setRun(r);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "加载运行详情失败");
      nav("/runs");
    } finally {
      setLoading(false);
    }
  }, [id, nav]);

  // poll while active
  useEffect(() => {
    void loadRun();
    if (!isActive) return;
    const timer = setInterval(loadRun, 2000);
    return () => clearInterval(timer);
  }, [loadRun, isActive]);

  // tab data + 日志自动刷新
  useEffect(() => {
    if (!id) return;

    const loadTabData = () => {
      if (tab === "metrics") {
        rApi.metrics(id).then(setMetrics).catch(() => {});
      } else if (tab === "logs") {
        rApi.logs(id).then(setLogs).catch(() => setLogs("暂无日志"));
      }
    };

    loadTabData();

    if ((tab === "logs" || tab === "metrics") && isActive) {
      const timer = setInterval(loadTabData, 3000);
      return () => clearInterval(timer);
    }
  }, [id, tab, isActive]);

  const handleCancel = async () => {
    if (!id) return;
    await rApi.cancel(id);
    await loadRun();
  };

  const handleDelete = async () => {
    if (!id) return;
    if (!confirm("确定删除此运行记录？")) return;
    await rApi.delete(id);
    nav(-1);
  };

  if (loading) return <p className="text-sm text-text-secondary">加载中...</p>;
  if (!run) return null;

  const progress = run.progress;
  const progressPct =
    progress && progress.total_cases > 0
      ? Math.round(
          (progress.completed_cases / progress.total_cases) * 100,
        )
      : 0;

  const tabs: { key: Tab; label: string }[] = [
    { key: "events", label: "事件" },
    { key: "metrics", label: "指标" },
    { key: "logs", label: "日志" },
  ];

  return (
    <>
      {/* Header */}
      <div className="mb-8">
        <Link
          to={`/pipelines/${run.pipeline_id}`}
          className="mb-4 inline-flex items-center gap-1 text-sm text-text-secondary hover:text-text"
        >
          <ChevronLeft className="h-4 w-4" />
          流水线
        </Link>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="font-mono text-2xl font-semibold font-display text-text">
              运行 {run.id}
            </h1>
            <RunStatusBadge status={run.status} />
            {isActive && (
              <span className="inline-flex items-center gap-1 text-xs text-text-secondary">
                {connected ? (
                  <Wifi className="h-3.5 w-3.5 text-status-success-text" />
                ) : (
                  <WifiOff className="h-3.5 w-3.5 text-red-500" />
                )}
                {connected ? "已连接" : "已断开"}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {isActive && (
              <button
                onClick={handleCancel}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-btn border border-border px-3.5 py-2 text-sm font-medium text-text transition-colors hover:bg-status-error hover:text-status-error-text"
              >
                <Ban className="h-4 w-4" />
                取消
              </button>
            )}
            {!isActive && (
              <button
                onClick={handleDelete}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-btn border border-border px-3.5 py-2 text-sm font-medium text-text transition-colors hover:bg-status-error hover:text-status-error-text"
              >
                <Trash2 className="h-4 w-4" />
                删除
              </button>
            )}
          </div>
        </div>

        {/* Time info */}
        <div className="mt-3 flex items-center gap-6 text-sm text-text-secondary">
          <span className="inline-flex items-center gap-1.5">
            <Clock className="h-3.5 w-3.5" />
            开始：{fmtDate(run.started_at)}
          </span>
          {run.completed_at && (
            <span>完成：{fmtDate(run.completed_at)}</span>
          )}
        </div>

        {/* Error */}
        {run.error && (
          <div className="mt-4 rounded-btn border border-status-error-text/20 bg-status-error px-4 py-3 text-sm text-status-error-text">
            {run.error}
          </div>
        )}

        {/* Progress bar */}
        {progress && progress.total_cases > 0 && (
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between text-xs text-text-secondary">
              <span>
                {progress.current_stage} — {progress.completed_cases}/
                {progress.total_cases} 个用例
                {progress.failed_cases > 0 && (
                  <span className="text-red-600">
                    {" "}
                    （{progress.failed_cases} 个失败）
                  </span>
                )}
              </span>
              <span>{progressPct}%</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-surface-active">
              <div
                className="h-full rounded-full bg-brand transition-all duration-500"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="mb-6 flex gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`cursor-pointer border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
              tab === t.key
                ? "border-brand text-text"
                : "border-transparent text-text-secondary hover:text-text"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Events */}
      {tab === "events" && (
        <div>
          {eventCaseIds.length > 0 && (
            <div className="mb-3">
              <select
                value={eventCaseFilter}
                onChange={(e) => setEventCaseFilter(e.target.value)}
                className="cursor-pointer rounded-btn border border-border bg-surface-raised px-3 py-1.5 text-sm text-text focus:border-brand focus:outline-none"
              >
                <option value="">全部用例</option>
                {eventCaseIds.map((cid) => (
                  <option key={cid} value={cid}>
                    用例: {cid}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="max-h-[calc(100vh-280px)] overflow-y-auto rounded-card border border-border bg-surface-raised p-4">
            {filteredEvents.length === 0 ? (
              <p className="text-sm text-text-muted">
                {isActive ? "等待事件..." : "暂无事件记录"}
              </p>
            ) : (
              <div className="space-y-2">
                {filteredEvents.map((ev, i) => (
                  <div key={i} className="flex items-start gap-2 text-sm">
                    <span className="flex-shrink-0 font-mono text-xs text-text-muted">
                      {new Date(ev.timestamp * 1000).toLocaleTimeString()}
                    </span>
                    <span
                      className={`flex-shrink-0 rounded px-1.5 py-0.5 font-mono text-xs ${eventBadgeClass(ev.type)}`}
                    >
                      {ev.type}
                    </span>
                    {!!ev.data.case_id && (
                      <span className="flex-shrink-0 rounded bg-violet-50 px-1.5 py-0.5 font-mono text-xs text-violet-700">
                        {ev.data.case_id as string}
                      </span>
                    )}
                    {!!ev.data.step && (
                      <span className="flex-shrink-0 text-xs text-text-secondary">
                        {ev.data.step as string}
                      </span>
                    )}
                    {!!ev.data.stage && !ev.data.step && (
                      <span className="flex-shrink-0 text-xs font-medium text-text-secondary">
                        {ev.data.stage as string}
                      </span>
                    )}
                    {!!ev.data.status && (
                      <span className="text-xs text-text-muted">
                        ({ev.data.status as string})
                      </span>
                    )}
                    {ev.data.duration_ms != null && (
                      <span className="text-xs text-text-muted">
                        {fmtDuration(ev.data.duration_ms as number)}
                      </span>
                    )}
                  </div>
                ))}
                <div ref={eventsEndRef} />
              </div>
            )}
          </div>
        </div>
      )}

      {/* Metrics */}
      {tab === "metrics" && (
        <div>
          {metrics ? (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-4">
                <MetricCard
                  label="用例总数"
                  value={metrics.total_cases}
                />
                <MetricCard
                  label="已完成"
                  value={metrics.completed_cases}
                  color="text-status-success-text"
                />
                <MetricCard
                  label="失败"
                  value={metrics.failed_cases}
                  color="text-red-600"
                />
              </div>
              {metrics.summary && metrics.summary.length > 0 && (
                <div>
                  <h3 className="mb-2 text-sm font-medium text-text">
                    汇总
                  </h3>
                  <pre className="overflow-x-auto rounded-btn border border-border bg-surface-active p-3 font-mono text-sm text-text">
                    {metrics.summary}
                  </pre>
                </div>
              )}
              <CaseMetricsTable cases={metrics.cases} />
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-text-muted">
              暂无指标数据
            </p>
          )}
        </div>
      )}

      {/* Logs */}
      {tab === "logs" && (
        <div>
          {isActive && (
            <p className="mb-2 text-xs text-text-muted">日志每 3 秒自动刷新</p>
          )}
          <pre className="max-h-[calc(100vh-280px)] overflow-auto rounded-card border border-border bg-surface-raised p-4 font-mono text-xs leading-relaxed text-text">
            {logs || "暂无日志"}
          </pre>
        </div>
      )}
    </>
  );
}

function CaseMetricsTable({ cases }: { cases: Record<string, CaseMetrics> }) {
  const entries = Object.entries(cases);
  if (entries.length === 0) return null;

  // Collect all unique custom metric keys across all cases
  const metricKeys = [
    ...new Set(entries.flatMap(([, c]) => Object.keys(c.metrics))),
  ];
  if (metricKeys.length === 0) return null;

  // Compute aggregated stats for numeric metrics
  const aggregated = metricKeys.map((key) => {
    const values = entries
      .map(([, c]) => c.metrics[key])
      .filter((v): v is number => typeof v === "number");
    if (values.length === 0) return { key, avg: null, min: null, max: null };
    const avg = values.reduce((a, b) => a + b, 0) / values.length;
    return {
      key,
      avg,
      min: Math.min(...values),
      max: Math.max(...values),
    };
  });

  const numericAgg = aggregated.filter((a) => a.avg !== null);

  return (
    <div className="space-y-4">
      {/* Aggregated numeric metrics */}
      {numericAgg.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-medium text-text">
            自定义指标汇总
          </h3>
          <div className="overflow-hidden rounded-card border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-active text-left text-xs font-medium uppercase tracking-wider text-text-secondary">
                  <th className="px-4 py-3">指标</th>
                  <th className="px-4 py-3">平均值</th>
                  <th className="px-4 py-3">最小值</th>
                  <th className="px-4 py-3">最大值</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {numericAgg.map((a) => (
                  <tr key={a.key} className="bg-surface-raised">
                    <td className="px-4 py-3 font-medium text-text">
                      {a.key}
                    </td>
                    <td className="px-4 py-3 font-mono text-text-secondary">
                      {a.avg!.toFixed(4)}
                    </td>
                    <td className="px-4 py-3 font-mono text-text-secondary">
                      {a.min!.toFixed(4)}
                    </td>
                    <td className="px-4 py-3 font-mono text-text-secondary">
                      {a.max!.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Per-case detail */}
      <div>
        <h3 className="mb-2 text-sm font-medium text-text">
          各用例指标
        </h3>
        <div className="overflow-hidden rounded-card border border-border">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-active text-left text-xs font-medium uppercase tracking-wider text-text-secondary">
                  <th className="px-4 py-3">用例 ID</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">耗时</th>
                  {metricKeys.map((k) => (
                    <th key={k} className="px-4 py-3">
                      {k}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {entries.map(([caseId, c]) => (
                  <tr key={caseId} className="bg-surface-raised hover:bg-surface-hover">
                    <td className="px-4 py-3 font-mono text-xs text-text-secondary">
                      {caseId}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                          c.status === "success"
                            ? "bg-status-success text-status-success-text"
                            : c.status === "failed"
                              ? "bg-status-error text-status-error-text"
                              : "bg-surface-active text-text-secondary"
                        }`}
                      >
                        {c.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-text-secondary">
                      {fmtDuration(c.duration_ms)}
                    </td>
                    {metricKeys.map((k) => (
                      <td
                        key={k}
                        className="px-4 py-3 font-mono text-xs text-text-secondary"
                      >
                        {c.metrics[k] != null
                          ? typeof c.metrics[k] === "number"
                            ? (c.metrics[k] as number).toFixed(4)
                            : String(c.metrics[k])
                          : "-"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color?: string;
}) {
  return (
    <div className="rounded-card border border-border bg-surface-raised shadow-card px-4 py-3">
      <p className="text-xs text-text-secondary">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${color || "text-text"}`}>
        {value}
      </p>
    </div>
  );
}
