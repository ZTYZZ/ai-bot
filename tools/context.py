"""共享上下文 — 避免循环导入。app.py 在启动时注入依赖。"""
_memory = None
_feishu_client = None


def set_memory(m):
    global _memory
    _memory = m


def set_feishu_client(c):
    global _feishu_client
    _feishu_client = c


def get_memory():
    return _memory


def get_feishu_client():
    return _feishu_client
