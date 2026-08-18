# DeepFlow Console

Pipeline 管理和运行监控的 Web 界面。

## 技术栈

React 18 + TypeScript + Vite + Tailwind CSS + React Router + Lucide React

## 目录结构

```
web/src/
  api/          API 客户端，封装所有后端请求
  types/        TypeScript 类型定义，对应后端 models
  hooks/        自定义 hooks (WebSocket 等)
  components/   通用 UI 组件 (Layout, Modal, StatusBadge)
  pages/
    PipelineList.tsx     Pipeline 列表和创建
    PipelineDetail.tsx   Manifest 编辑、组件管理、运行历史
    RunDetail.tsx        实时事件、Case 表格、Metrics、日志
    RecentRuns.tsx       跨 Pipeline 运行总览
  App.tsx       路由定义
  main.tsx      入口
```

## 开发

前置要求：Node.js >= 18、pnpm >= 9，后端已启动在 `:8000`。

```bash
cd web
pnpm install
pnpm dev
```

Vite dev server 启动在 `http://localhost:5173`，`/api` 请求自动代理到 `http://localhost:8000`。

## 构建

```bash
pnpm build
```

产物输出到 `../src/deepflow/server/static/`，由后端自动检测并提供服务。构建与分发的完整说明见 [docs/BUILD.md](../docs/BUILD.md)。
