# 内置组件

框架自带的工具组件，通过 `builtin:` 前缀引用。

## builtin:clean_workspace

preprocess 阶段。删除 workspace 目录下的所有内容，重建 workspace 和 metrics 子目录。在 pipeline 开始前使用，确保每次运行从干净状态开始。

无配置。

```yaml
- src: builtin:clean_workspace
```

## builtin:clean_casespace

casewise 阶段。删除当前 case 的 casespace 目录中的所有文件。用于在 casewise 阶段释放磁盘空间。

无配置。

```yaml
- src: builtin:clean_casespace
```
