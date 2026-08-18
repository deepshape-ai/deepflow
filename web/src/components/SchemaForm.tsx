interface JsonSchemaProperty {
  type?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  title?: string;
}

interface JsonSchema {
  type?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
}

interface Props {
  schema: JsonSchema;
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
}

export function SchemaForm({ schema, values, onChange }: Props) {
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const entries = Object.entries(properties);

  if (entries.length === 0) {
    return (
      <p className="text-xs text-text-muted">该组件无需配置</p>
    );
  }

  const update = (key: string, value: unknown) => {
    onChange({ ...values, [key]: value });
  };

  return (
    <div className="space-y-2.5">
      {entries.map(([key, prop]) => (
        <div key={key}>
          <label className="mb-1 flex items-center gap-1 text-xs font-medium text-text-secondary">
            {prop.title ?? key}
            {required.has(key) && <span className="text-red-500">*</span>}
          </label>

          {prop.enum ? (
            <select
              value={String(values[key] ?? prop.default ?? "")}
              onChange={(e) => update(key, e.target.value)}
              className="w-full rounded border border-border bg-surface-input px-2 py-1.5 text-sm focus:border-brand focus:outline-none"
            >
              <option value="">选择...</option>
              {prop.enum.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          ) : prop.type === "boolean" ? (
            <label className="inline-flex items-center gap-2 text-sm text-text-secondary">
              <input
                type="checkbox"
                checked={Boolean(values[key] ?? prop.default ?? false)}
                onChange={(e) => update(key, e.target.checked)}
                className="h-4 w-4 rounded border-border text-brand focus:ring-brand"
              />
              {prop.description}
            </label>
          ) : prop.type === "number" || prop.type === "integer" ? (
            <input
              type="number"
              value={values[key] != null ? String(values[key]) : (prop.default != null ? String(prop.default) : "")}
              placeholder={prop.description}
              onChange={(e) => update(key, e.target.value === "" ? undefined : Number(e.target.value))}
              className="w-full rounded border border-border bg-surface-input px-2 py-1.5 text-sm focus:border-brand focus:outline-none"
            />
          ) : (
            <input
              type="text"
              value={String(values[key] ?? prop.default ?? "")}
              placeholder={prop.description}
              onChange={(e) => update(key, e.target.value || undefined)}
              className="w-full rounded border border-border bg-surface-input px-2 py-1.5 text-sm focus:border-brand focus:outline-none"
            />
          )}

          {prop.description && prop.type !== "boolean" && (
            <p className="mt-0.5 text-xs text-text-muted">{prop.description}</p>
          )}
        </div>
      ))}
    </div>
  );
}
