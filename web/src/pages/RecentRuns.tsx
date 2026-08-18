import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Clock } from "lucide-react";
import { pipelines as pApi, runs as rApi } from "../api/client";
import type { Pipeline, RunResponse } from "../types";
import { RunStatusBadge } from "../components/StatusBadge";

interface RunWithPipeline extends RunResponse {
  pipeline_name: string;
}

function fmtDate(s: string | null) {
  return s ? new Date(s).toLocaleString() : "-";
}

export function RecentRuns() {
  const nav = useNavigate();
  const [items, setItems] = useState<RunWithPipeline[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const allPipelines = await pApi.list();
        const map = new Map<string, Pipeline>();
        allPipelines.forEach((p) => map.set(p.id, p));

        const allRuns: RunWithPipeline[] = [];
        for (const p of allPipelines) {
          const runs = await rApi.list(p.id);
          for (const r of runs) {
            allRuns.push({ ...r, pipeline_name: p.name });
          }
        }
        allRuns.sort(
          (a, b) =>
            new Date(b.created_at).getTime() -
            new Date(a.created_at).getTime(),
        );
        setItems(allRuns);
      } catch {
        /* empty */
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <>
      <h1 className="mb-8 text-2xl font-semibold font-display text-text">
        运行记录
      </h1>

      {loading && <p className="text-sm text-text-secondary">加载中...</p>}

      {!loading && items.length === 0 && (
        <p className="py-8 text-center text-sm text-text-muted">
          暂无运行记录
        </p>
      )}

      {items.length > 0 && (
        <div className="overflow-hidden rounded-card border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-active text-left text-xs font-medium uppercase tracking-wider text-text-secondary">
                <th className="px-5 py-3.5">运行 ID</th>
                <th className="px-5 py-3.5">流水线</th>
                <th className="px-5 py-3.5">状态</th>
                <th className="px-5 py-3.5">开始时间</th>
                <th className="px-5 py-3.5">完成时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((r) => (
                <tr
                  key={r.id}
                  className="cursor-pointer bg-surface-raised transition-colors hover:bg-surface-hover"
                  onClick={() => nav(`/runs/${r.id}`)}
                >
                  <td className="px-5 py-3.5 font-mono text-xs text-text-secondary">
                    {r.id}
                  </td>
                  <td className="px-5 py-3.5 text-text">
                    {r.pipeline_name}
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
    </>
  );
}
