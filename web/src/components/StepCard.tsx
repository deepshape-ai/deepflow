import { useState } from "react";
import { ChevronRight, ChevronDown, X, ArrowUp, ArrowDown } from "lucide-react";
import type { StepConfig, ComponentInfo, CustomComponentInfo } from "../types";
import { KeyValueEditor } from "./KeyValueEditor";
import { SchemaForm } from "./SchemaForm";

interface Props {
  step: StepConfig;
  index: number;
  total: number;
  stage: string;
  builtinComponents: ComponentInfo[];
  customComponents: CustomComponentInfo[];
  onChange: (step: StepConfig) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}

function configToKV(config: Record<string, unknown>): { key: string; value: string }[] {
  return Object.entries(config).map(([key, value]) => ({
    key,
    value: typeof value === "string" ? value : JSON.stringify(value),
  }));
}

function kvToConfig(pairs: { key: string; value: string }[]): Record<string, unknown> {
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

export function StepCard({
  step,
  index,
  total,
  stage,
  builtinComponents,
  customComponents,
  onChange,
  onRemove,
  onMoveUp,
  onMoveDown,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  const filteredBuiltins = builtinComponents.filter((c) => c.stage === stage);
  const filteredCustom = customComponents.filter((c) => c.stage === stage || c.stage === null);

  // 查找当前选中组件的 schema
  const findSchema = (): Record<string, unknown> | null => {
    if (!step.src) return null;
    // namespace:name 格式（如 builtin:clean_workspace）
    if (step.src.includes(":") && !step.src.startsWith("./")) {
      const comp = builtinComponents.find((c) => c.name === step.src);
      return comp?.config_schema && Object.keys(comp.config_schema).length > 0
        ? comp.config_schema
        : null;
    }
    const filename = step.src.replace("./components/", "");
    const comp = customComponents.find((c) => c.filename === filename);
    return comp?.config_schema ?? null;
  };

  const schema = findSchema();
  const configPairs = configToKV(step.config || {});

  return (
    <div className="rounded-card border border-border bg-surface-raised">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          onClick={() => setExpanded(!expanded)}
          className="cursor-pointer p-0.5 text-text-muted"
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
        <span className="text-xs text-text-muted">#{index + 1}</span>
        <select
          value={step.src}
          onChange={(e) => onChange({ ...step, src: e.target.value, config: {} })}
          className="flex-1 rounded border border-border bg-surface-input px-2 py-1 text-sm focus:border-brand focus:outline-none"
        >
          <option value="">选择组件...</option>
          {filteredBuiltins.length > 0 && (
            <optgroup label="内置组件">
              {filteredBuiltins.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
            </optgroup>
          )}
          {filteredCustom.length > 0 && (
            <optgroup label="自定义组件">
              {filteredCustom.map((c) => (
                <option key={c.filename} value={`./components/${c.filename}`}>
                  ./components/{c.filename}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        <div className="flex items-center gap-0.5">
          <button onClick={onMoveUp} disabled={index === 0} className="cursor-pointer p-0.5 text-text-muted hover:text-text disabled:opacity-30">
            <ArrowUp className="h-3.5 w-3.5" />
          </button>
          <button onClick={onMoveDown} disabled={index === total - 1} className="cursor-pointer p-0.5 text-text-muted hover:text-text disabled:opacity-30">
            <ArrowDown className="h-3.5 w-3.5" />
          </button>
          <button onClick={onRemove} className="cursor-pointer p-0.5 text-text-muted hover:text-red-500">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-border px-3 py-3 space-y-3">
          <div>
            <p className="mb-1.5 text-xs font-medium text-text-secondary">配置</p>
            {schema ? (
              <SchemaForm
                schema={schema as unknown as { properties?: Record<string, { type?: string; description?: string; default?: unknown; enum?: string[]; title?: string }>; required?: string[] }}
                values={step.config || {}}
                onChange={(values) => onChange({ ...step, config: values })}
              />
            ) : (
              <KeyValueEditor
                pairs={configPairs.length > 0 ? configPairs : []}
                onChange={(pairs) =>
                  onChange({ ...step, config: kvToConfig(pairs) })
                }
              />
            )}
          </div>

          <div>
            <p className="mb-1.5 text-xs font-medium text-text-secondary">
              重试（留空则不启用）
            </p>
            <div className="flex items-center gap-3">
              <label className="text-xs text-text-secondary">
                最大次数
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={step.retry?.max_attempts ?? ""}
                  placeholder="1"
                  onChange={(e) => {
                    const v = e.target.value ? parseInt(e.target.value) : undefined;
                    onChange({ ...step, retry: { ...step.retry, max_attempts: v } });
                  }}
                  className="ml-1 w-16 rounded border border-border bg-surface-input px-2 py-0.5 text-sm focus:border-brand focus:outline-none"
                />
              </label>
              <label className="text-xs text-text-secondary">
                间隔(秒)
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  value={step.retry?.delay ?? ""}
                  placeholder="1"
                  onChange={(e) => {
                    const v = e.target.value ? parseFloat(e.target.value) : undefined;
                    onChange({ ...step, retry: { ...step.retry, delay: v } });
                  }}
                  className="ml-1 w-16 rounded border border-border bg-surface-input px-2 py-0.5 text-sm focus:border-brand focus:outline-none"
                />
              </label>
              <label className="text-xs text-text-secondary">
                退避
                <select
                  value={step.retry?.backoff ?? "fixed"}
                  onChange={(e) =>
                    onChange({ ...step, retry: { ...step.retry, backoff: e.target.value as "fixed" | "exponential" } })
                  }
                  className="ml-1 rounded border border-border bg-surface-input px-2 py-0.5 text-sm focus:border-brand focus:outline-none"
                >
                  <option value="fixed">固定</option>
                  <option value="exponential">指数</option>
                </select>
              </label>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
