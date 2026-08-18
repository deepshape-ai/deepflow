import { Plus } from "lucide-react";
import type { StepConfig, ComponentInfo, CustomComponentInfo } from "../types";
import { StepCard } from "./StepCard";

interface Props {
  label: string;
  stage: string;
  steps: StepConfig[];
  builtinComponents: ComponentInfo[];
  customComponents: CustomComponentInfo[];
  onChange: (steps: StepConfig[]) => void;
}

export function StageEditor({
  label,
  stage,
  steps,
  builtinComponents,
  customComponents,
  onChange,
}: Props) {
  const addStep = () => {
    onChange([...steps, { src: "", config: {} }]);
  };

  const updateStep = (index: number, step: StepConfig) => {
    onChange(steps.map((s, i) => (i === index ? step : s)));
  };

  const removeStep = (index: number) => {
    onChange(steps.filter((_, i) => i !== index));
  };

  const moveStep = (from: number, to: number) => {
    if (to < 0 || to >= steps.length) return;
    const next = [...steps];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved!);
    onChange(next);
  };

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-medium text-text">{label}</h3>
        <button
          onClick={addStep}
          className="inline-flex cursor-pointer items-center gap-1 text-xs text-brand-light hover:text-brand"
        >
          <Plus className="h-3 w-3" />
          添加 Step
        </button>
      </div>
      <div className="space-y-2">
        {steps.length === 0 && (
          <p className="py-3 text-center text-xs text-text-muted">暂无步骤</p>
        )}
        {steps.map((step, i) => (
          <StepCard
            key={i}
            step={step}
            index={i}
            total={steps.length}
            stage={stage}
            builtinComponents={builtinComponents}
            customComponents={customComponents}
            onChange={(s) => updateStep(i, s)}
            onRemove={() => removeStep(i)}
            onMoveUp={() => moveStep(i, i - 1)}
            onMoveDown={() => moveStep(i, i + 1)}
          />
        ))}
      </div>
    </div>
  );
}
