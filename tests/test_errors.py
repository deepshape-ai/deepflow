"""FatalError 异常测试。"""
from deepflow.core.errors import FatalError


def test_fatal_error_is_exception():
    err = FatalError("认证失败")
    assert isinstance(err, Exception)
    assert str(err) == "认证失败"


def test_fatal_error_not_caught_by_generic_retry():
    """FatalError 应当能被 isinstance 检查区分。"""
    err = FatalError("不可恢复")
    assert isinstance(err, FatalError)
    assert not isinstance(ValueError("普通错误"), FatalError)
