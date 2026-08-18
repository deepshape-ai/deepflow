export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type CaseStatus = "pending" | "running" | "success" | "failed";

export interface Pipeline {
  id: string;
  name: string;
  manifest: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProgressInfo {
  current_stage: string;
  completed_cases: number;
  total_cases: number;
  failed_cases: number;
}

export interface RunResponse {
  id: string;
  pipeline_id: string;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  progress: ProgressInfo | null;
  error: string | null;
}

export interface CaseResponse {
  case_id: string;
  status: CaseStatus;
  metrics: Record<string, unknown>;
  duration_ms: number | null;
}

export interface CaseMetrics {
  metrics: Record<string, unknown>;
  status: string;
  duration_ms: number;
}

export interface MetricsResponse {
  run_id: string;
  summary: string;
  total_cases: number;
  completed_cases: number;
  failed_cases: number;
  cases: Record<string, CaseMetrics>;
}

export interface ComponentInfo {
  name: string;
  description: string;
  stage: string;
  config_schema: Record<string, unknown>;
}

export interface CustomComponentInfo {
  filename: string;
  class_name: string | null;
  stage: string | null;
  description: string;
  config_schema: Record<string, unknown> | null;
}

export interface ComponentContent {
  filename: string;
  content: string;
}

export interface RunEvent {
  type: string;
  run_id: string;
  timestamp: number;
  data: Record<string, unknown>;
}

// ── Manifest 结构化类型 ──

export interface RetryConfig {
  max_attempts?: number;
  delay?: number;
  backoff?: "fixed" | "exponential";
}

export interface StepConfig {
  src: string;
  config?: Record<string, unknown>;
  retry?: RetryConfig;
}

export interface PipelineStages {
  preprocess: StepConfig[];
  casewise: StepConfig[];
  postprocess: StepConfig[];
}

export interface ManifestData {
  version: string;
  name: string;
  workspace?: string;
  concurrency?: number;
  vars?: Record<string, unknown>;
  pipeline: PipelineStages;
}
