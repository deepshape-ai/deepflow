import { useEffect, useState } from "react";
import type { ManifestData, StepConfig, ComponentInfo, CustomComponentInfo } from "../types";
import { KeyValueEditor } from "./KeyValueEditor";
import { StageEditor } from "./StageEditor";
import { EnvVarStatus } from "./EnvVarStatus";
import { builtinComponents as bApi, pipelineComponents as cApi } from "../api/client";

interface Props {
  manifest: ManifestData;
  pipelineId: string;
  onChange: (manifest: ManifestData) => void;
}

/** vars 转 KV */
function varsToKV(
  vars: Record<string, unknown> | undefined,
): { key: string; value: string }[] {
  if (!vars) return [];
  return Object.entries(vars).map(([key, value]) => ({
    key,
    value: typeof value === "string" ? value : JSON.stringify(value),
  }));
}

function kvToVars(pairs: { key: string; value: string }[]): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const { key, value } of pairs) {
    if (!key) continue;
    try {
      result[key] = JSON.parse(value);
    } catch {
      result[key] = value;
    }
  }
  return result;
}

export function ManifestFormEditor({ manifest, pipelineId, onChange }: Props) {
  const [builtins, setBuiltins] = useState<ComponentInfo[]>([]);
  const [customComps, setCustomComps] = useState<CustomComponentInfo[]>([]);

  useEffect(() => {
    bApi.list().then(setBuiltins).catch(() => {});
    cApi.list(pipelineId).then(setCustomComps).catch(() => {});
  }, [pipelineId]);

  const updateVars = (pairs: { key: string; value: string }[]) => {
    onChange({ ...manifest, vars: kvToVars(pairs) });
  };

  const updateStage = (stage: keyof ManifestData["pipeline"], steps: StepConfig[]) => {
    onChange({
      ...manifest,
      pipeline: { ...manifest.pipeline, [stage]: steps },
    });
  };

  const varPairs = varsToKV(manifest.vars);

  return (
    <div className="space-y-8">
      {/* 基础配置 */}
      <div className="space-y-4">
        <h3 className="text-sm font-medium text-text">基础配置</h3>
        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-xs text-text-secondary">Pipeline 名称</span>
            <input
              value={manifest.name}
              onChange={(e) => onChange({ ...manifest, name: e.target.value })}
              className="mt-1.5 block w-full rounded-btn border border-border bg-surface-input px-3 py-2 text-sm focus:border-brand focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs text-text-secondary">版本</span>
            <input
              value={manifest.version}
              onChange={(e) => onChange({ ...manifest, version: e.target.value })}
              className="mt-1.5 block w-full rounded-btn border border-border bg-surface-input px-3 py-2 text-sm focus:border-brand focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs text-text-secondary">并发数</span>
            <div className="mt-1 flex items-center gap-2">
              <input
                type="range"
                min={1}
                max={100}
                value={manifest.concurrency ?? 1}
                onChange={(e) => onChange({ ...manifest, concurrency: parseInt(e.target.value) })}
                className="flex-1"
              />
              <span className="w-8 text-center text-sm text-text">
                {manifest.concurrency ?? 1}
              </span>
            </div>
          </label>
        </div>

        {/* Vars */}
        <div>
          <p className="mb-1.5 text-xs text-text-secondary">变量 (vars)</p>
          <KeyValueEditor pairs={varPairs} onChange={updateVars} />
        </div>
      </div>

      {/* Pipeline Stages */}
      <StageEditor
        label="Preprocess"
        stage="preprocess"
        steps={manifest.pipeline.preprocess}
        builtinComponents={builtins}
        customComponents={customComps}
        onChange={(steps) => updateStage("preprocess", steps)}
      />
      <StageEditor
        label="Casewise"
        stage="casewise"
        steps={manifest.pipeline.casewise}
        builtinComponents={builtins}
        customComponents={customComps}
        onChange={(steps) => updateStage("casewise", steps)}
      />
      <StageEditor
        label="Postprocess"
        stage="postprocess"
        steps={manifest.pipeline.postprocess}
        builtinComponents={builtins}
        customComponents={customComps}
        onChange={(steps) => updateStage("postprocess", steps)}
      />

      {/* Env Var Status */}
      <EnvVarStatus manifest={manifest as unknown as Record<string, unknown>} />
    </div>
  );
}
