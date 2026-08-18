import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";

export function Layout() {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl px-10 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
