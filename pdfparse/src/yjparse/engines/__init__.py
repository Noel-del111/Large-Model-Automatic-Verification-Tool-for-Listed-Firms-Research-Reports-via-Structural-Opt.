"""解析引擎适配层。

每个引擎实现统一接口，输出带页码与坐标的 Page 列表；
引擎是否可用由 available() 判定，缺失时给出安装提示而不是直接崩溃。
"""

from .base import BaseEngine, EngineCapabilities, EngineUnavailable, build_engine, registry  # noqa: F401
