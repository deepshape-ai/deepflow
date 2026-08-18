import { useEffect, useState } from "react";
import { utils } from "../api/client";

interface Props {
  manifest: Record<string, unknown>;
}

/** 递归提取所有 ${VAR} 引用 */
function extractEnvVars(data: unknown): string[] {
  const vars: string[] = [];
  const pattern = /\$\{([^}]+)\}/g;
  const walk = (val: unknown) => {
    if (typeof val === "string") {
      let match;
      while ((match = pattern.exec(val)) !== null) {
        vars.push(match[1]!);
      }
    } else if (Array.isArray(val)) {
      val.forEach(walk);
    } else if (val && typeof val === "object") {
      Object.values(val).forEach(walk);
    }
  };
  walk(data);
  return [...new Set(vars)];
}

export function EnvVarStatus({ manifest }: Props) {
  const [status, setStatus] = useState<Record<string, boolean>>({});
  const vars = extractEnvVars(manifest);

  useEffect(() => {
    if (vars.length === 0) return;
    utils.checkEnv(vars).then(setStatus).catch(() => {});
  }, [JSON.stringify(vars)]);

  if (vars.length === 0) return null;

  return (
    <div className="rounded-card border border-border bg-surface-raised px-3 py-2">
      <p className="mb-1.5 text-xs font-medium text-text-secondary">环境变量</p>
      <div className="space-y-1">
        {vars.map((v) => (
          <div key={v} className="flex items-center gap-2 text-sm">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                status[v] ? "bg-status-success-text" : "bg-text-helper"
              }`}
            />
            <span className="font-mono text-xs text-text">{v}</span>
            <span className="text-xs text-text-muted">
              {status[v] === undefined ? "" : status[v] ? "已设置" : "未设置"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
