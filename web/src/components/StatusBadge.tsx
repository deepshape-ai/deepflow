import { Loader, CheckCircle, XCircle, Ban, Clock } from "lucide-react";
import type { RunStatus, CaseStatus } from "../types";

const runStyles: Record<RunStatus, string> = {
  pending: "bg-surface-active text-text-secondary",
  running: "bg-brand-50 text-brand-light",
  completed: "bg-status-success text-status-success-text",
  failed: "bg-status-error text-status-error-text",
  cancelled: "bg-surface-active text-text-muted",
};

const runLabels: Record<RunStatus, string> = {
  pending: "等待中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const caseStyles: Record<CaseStatus, string> = {
  pending: "bg-surface-active text-text-secondary",
  running: "bg-brand-50 text-brand-light",
  success: "bg-status-success text-status-success-text",
  failed: "bg-status-error text-status-error-text",
};

const caseLabels: Record<CaseStatus, string> = {
  pending: "等待中",
  running: "运行中",
  success: "成功",
  failed: "失败",
};

const StatusIcon = ({ status }: { status: string }) => {
  const cls = "w-3.5 h-3.5";
  switch (status) {
    case "pending":
      return <Clock className={cls} />;
    case "running":
      return <Loader className={`${cls} animate-spin`} />;
    case "completed":
    case "success":
      return <CheckCircle className={cls} />;
    case "failed":
      return <XCircle className={cls} />;
    case "cancelled":
      return <Ban className={cls} />;
    default:
      return null;
  }
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${runStyles[status]}`}
    >
      <StatusIcon status={status} />
      {runLabels[status]}
    </span>
  );
}

export function CaseStatusBadge({ status }: { status: CaseStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${caseStyles[status]}`}
    >
      <StatusIcon status={status} />
      {caseLabels[status]}
    </span>
  );
}
