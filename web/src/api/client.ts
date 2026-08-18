const BASE = "/api/v1";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// Pipelines
export const pipelines = {
  list: () => request<import("../types").Pipeline[]>("/pipelines"),
  get: (id: string) => request<import("../types").Pipeline>(`/pipelines/${id}`),
  create: (data: { name: string; manifest: Record<string, unknown> }) =>
    request<import("../types").Pipeline>("/pipelines", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (
    id: string,
    data: { name?: string; manifest?: Record<string, unknown> },
  ) =>
    request<import("../types").Pipeline>(`/pipelines/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    request<void>(`/pipelines/${id}`, { method: "DELETE" }),
  exportYaml: async (id: string) => {
    const res = await fetch(`${BASE}/pipelines/${id}/export`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, body.detail || res.statusText);
    }
    return res.text();
  },
  importYaml: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/pipelines/import`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, body.detail || res.statusText);
    }
    return res.json() as Promise<import("../types").Pipeline>;
  },
};

// Pipeline components (user-uploaded .py files)
export const pipelineComponents = {
  list: (pipelineId: string) =>
    request<import("../types").CustomComponentInfo[]>(`/pipelines/${pipelineId}/components`),
  get: (pipelineId: string, filename: string) =>
    request<import("../types").ComponentContent>(
      `/pipelines/${pipelineId}/components/${filename}`,
    ),
  upload: async (pipelineId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(
      `${BASE}/pipelines/${pipelineId}/components`,
      { method: "POST", body: form },
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, body.detail || res.statusText);
    }
    return res.json();
  },
  update: (pipelineId: string, filename: string, content: string) =>
    request<import("../types").CustomComponentInfo>(
      `/pipelines/${pipelineId}/components/${filename}`,
      { method: "PUT", body: JSON.stringify({ content }) },
    ),
  delete: (pipelineId: string, filename: string) =>
    request<void>(`/pipelines/${pipelineId}/components/${filename}`, {
      method: "DELETE",
    }),
};

// Runs
export const runs = {
  create: (pipelineId: string, overrides?: Record<string, unknown>) =>
    request<import("../types").RunResponse>(
      `/pipelines/${pipelineId}/runs`,
      {
        method: "POST",
        body: JSON.stringify(overrides ? { overrides } : {}),
      },
    ),
  get: (id: string) =>
    request<import("../types").RunResponse>(`/runs/${id}`),
  list: (pipelineId: string) =>
    request<import("../types").RunResponse[]>(
      `/pipelines/${pipelineId}/runs`,
    ),
  cancel: (id: string) =>
    request<void>(`/runs/${id}/cancel`, { method: "POST" }),
  delete: (id: string) =>
    request<void>(`/runs/${id}`, { method: "DELETE" }),
  cases: (id: string, status?: string) => {
    const q = status ? `?status_filter=${status}` : "";
    return request<import("../types").CaseResponse[]>(
      `/runs/${id}/cases${q}`,
    );
  },
  metrics: (id: string) =>
    request<import("../types").MetricsResponse>(`/runs/${id}/metrics`),
  logs: async (id: string) => {
    const res = await fetch(`${BASE}/runs/${id}/logs`);
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.text();
  },
};

// Built-in components
export const builtinComponents = {
  list: () => request<import("../types").ComponentInfo[]>("/components"),
};

// Utilities
export const utils = {
  validate: (manifest: Record<string, unknown>) =>
    request<{ valid: boolean; errors: string[] }>("/validate", {
      method: "POST",
      body: JSON.stringify({ manifest }),
    }),
  health: () => request<{ status: string }>("/health"),
  checkEnv: (keys: string[]) =>
    request<Record<string, boolean>>(`/env/check?keys=${keys.join(",")}`),
};
