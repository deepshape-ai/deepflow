# 构建与分发

## 前置要求

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | >= 3.10 | 运行时和构建 |
| Node.js | >= 18 | 前端编译（可选） |
| pnpm | >= 9 | 前端包管理 |

没有 Node.js 时仍可构建 wheel，产物不含前端界面（`deepflow serve` 仅提供 API）。

## 构建 wheel

```bash
pip wheel . -w dist/
```

Hatch 自定义 build hook (`hatch_build.py`) 在构建 wheel 时自动执行前端编译：

1. 检测 `pnpm-lock.yaml` (使用 pnpm) 或 `package-lock.json` (回退 npm)
2. 安装依赖并执行构建
3. Vite 产物写入 `src/deepflow/server/static/`
4. Hatch `force-include` 将 `static/` 打入 `.whl`

构建链路：

```
pip wheel .
    |
    v
hatch_build.py (build hook)
    | pnpm install && pnpm run build
    v
web/  --vite-->  src/deepflow/server/static/  --hatch-->  .whl
                   (vite.config.ts:outDir)   (pyproject.toml:force-include)
```

三处路径配置：

| 环节 | 配置位置 | 值 |
|------|----------|-----|
| Vite 输出 | `vite.config.ts` -> `build.outDir` | `../src/deepflow/server/static` |
| wheel 打包 | `pyproject.toml` -> `force-include` | `src/deepflow/server/static` -> `deepflow/server/static` |
| 运行时加载 | `app.py` -> `STATIC_DIR` | `Path(__file__).parent / "static"` |

## 部署

### 在线安装

拷贝一个 wheel 文件到目标机器即可，目标机器不需要 Node.js：

```bash
pip install "deepflow-0.3.0-py3-none-any.whl[server]"
deepflow serve
```

### 离线安装

`pip wheel . -w dist/` 会同时下载所有依赖的 wheel。将整个 `dist/` 目录拷贝到目标机器：

```bash
pip install --no-index --find-links dist/ "deepflow[server]"
deepflow serve
```

### 仅构建前端

```bash
cd web
pnpm install
pnpm build
# 产物输出到 src/deepflow/server/static/
```

然后开发模式安装后端：

```bash
pip install -e '.[server]'
deepflow serve
```
