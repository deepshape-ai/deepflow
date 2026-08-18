import { useState, useEffect } from "react";
import { Plus, X } from "lucide-react";

interface KVPair {
  key: string;
  value: string;
}

interface Props {
  pairs: KVPair[];
  onChange: (pairs: KVPair[]) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}

export function KeyValueEditor({
  pairs: externalPairs,
  onChange,
  keyPlaceholder = "key",
  valuePlaceholder = "value",
}: Props) {
  // 本地状态保留空行，外部 pairs 仅做初始化和同步
  const [pairs, setPairs] = useState(externalPairs);

  useEffect(() => {
    // 外部数据变化时同步，但保留本地已有的空行
    const emptyRows = pairs.filter((p) => !p.key && !p.value);
    const merged = [...externalPairs, ...emptyRows];
    setPairs(merged);
  }, [JSON.stringify(externalPairs)]);

  const update = (index: number, field: "key" | "value", val: string) => {
    const next = pairs.map((p, i) => (i === index ? { ...p, [field]: val } : p));
    setPairs(next);
    onChange(next);
  };

  const add = () => {
    // 仅更新本地状态，不通知上层（空行会被上层过滤）
    setPairs((prev) => [...prev, { key: "", value: "" }]);
  };

  const remove = (index: number) => {
    const next = pairs.filter((_, i) => i !== index);
    setPairs(next);
    onChange(next);
  };

  return (
    <div className="space-y-1.5">
      {pairs.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            value={p.key}
            onChange={(e) => update(i, "key", e.target.value)}
            placeholder={keyPlaceholder}
            className="w-40 rounded border border-border bg-surface-input px-2 py-1 text-sm focus:border-brand focus:outline-none"
          />
          <input
            value={p.value}
            onChange={(e) => update(i, "value", e.target.value)}
            placeholder={valuePlaceholder}
            className="flex-1 rounded border border-border bg-surface-input px-2 py-1 text-sm focus:border-brand focus:outline-none"
          />
          <button
            onClick={() => remove(i)}
            className="cursor-pointer p-0.5 text-text-muted hover:text-red-500"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <button
        onClick={add}
        className="inline-flex cursor-pointer items-center gap-1 text-xs text-brand-light hover:text-brand"
      >
        <Plus className="h-3 w-3" />
        添加字段
      </button>
    </div>
  );
}
