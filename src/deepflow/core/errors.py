"""不可恢复异常定义。"""


class FatalError(Exception):
    """不可恢复的致命错误。

    组件抛出 FatalError 时跳过重试，立即终止整个 pipeline。
    用于认证失败、关键资源不可用等不可恢复场景。
    """
