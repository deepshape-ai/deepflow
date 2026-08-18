import { Routes, Route } from "react-router-dom";
import { Layout } from "./components/Layout";
import { PipelineList } from "./pages/PipelineList";
import { PipelineDetail } from "./pages/PipelineDetail";
import { RunDetail } from "./pages/RunDetail";
import { RecentRuns } from "./pages/RecentRuns";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<PipelineList />} />
        <Route path="/pipelines/:id" element={<PipelineDetail />} />
        <Route path="/runs" element={<RecentRuns />} />
        <Route path="/runs/:id" element={<RunDetail />} />
      </Route>
    </Routes>
  );
}
