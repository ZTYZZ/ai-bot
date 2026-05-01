"""
Agent 工具注册中心。

使用 @register 装饰器来声明工具函数。
TOOLS 列表和 execute_tool 函数由 registry 自动生成。
"""

# 全局注册表
_registry: dict = {}


def register(name: str, description: str, parameters: dict):
    """装饰器：将一个函数注册为 Agent 工具。

    Args:
        name: 工具名称（OpenAI function name）
        description: 工具描述（AI 根据描述决定何时调用）
        parameters: JSON Schema 参数定义
    """

    def decorator(func):
        _registry[name] = {
            "function": func,
            "definition": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
        }
        return func

    return decorator


def get_tool_definitions() -> list:
    """返回所有已注册工具的 OpenAI function calling 定义列表。"""
    return [info["definition"] for info in _registry.values()]


def execute_tool(name: str, arguments: dict) -> str:
    """执行一个已注册的工具，返回结果字符串。

    Args:
        name: 工具名称
        arguments: 工具参数字典

    Returns:
        工具执行结果字符串（成功或错误信息）
    """
    if name not in _registry:
        return f"错误：未知工具 {name}。可用工具：{', '.join(_registry.keys())}"

    try:
        return _registry[name]["function"](arguments)
    except Exception as e:
        return f"工具 {name} 执行失败：{str(e)}"
