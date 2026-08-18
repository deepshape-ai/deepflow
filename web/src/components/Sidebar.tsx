import { NavLink } from "react-router-dom";
import { GitBranch, Activity } from "lucide-react";

const links = [
  { to: "/", icon: GitBranch, label: "流水线" },
  { to: "/runs", icon: Activity, label: "运行记录" },
];

export function Sidebar() {
  return (
    <aside className="flex h-screen w-56 flex-shrink-0 flex-col border-r border-border bg-surface-sidebar">
      <div className="flex h-14 items-center gap-2 border-b border-border px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand text-xs font-bold text-white shadow-glow">
          D
        </div>
        <span className="font-display text-sm font-semibold tracking-tight text-text">
          DeepFlow 控制台
        </span>
      </div>
      <nav className="flex-1 space-y-1 px-3 py-4">
        {links.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-pill px-3 py-2 text-sm font-medium transition-colors cursor-pointer ${
                isActive
                  ? "bg-brand-50 text-brand-light"
                  : "text-text-muted hover:bg-surface-hover hover:text-text"
              }`
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-border px-5 py-3">
        <p className="text-xs text-text-helper">DeepFlow Server</p>
      </div>
    </aside>
  );
}
