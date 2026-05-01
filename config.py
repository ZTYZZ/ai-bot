import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 飞书配置
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_VERIFY_TOKEN = os.getenv("FEISHU_VERIFY_TOKEN", "")

# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"

# 记忆配置
MAX_CONTEXT_MESSAGES = 20  # 每次对话携带的最大历史消息数
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.db")

# 数据库（PostgreSQL 持久化 / SQLite 本地）
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
