import os

# 国网电力官网
LOGIN_URL = "https://www.95598.cn/osgweb/login"
ELECTRIC_USAGE_URL = "https://www.95598.cn/osgweb/electricityCharge"
BALANCE_URL = "https://www.95598.cn/osgweb/userAcc"
BILL_SUMMARY_URL = "https://www.95598.cn/osgweb/electricityCharge"
STEP_ELECTRICITY_URL = "https://www.95598.cn/osgweb/stepElectricityConsumption"
ELECTRIC_BILL_SUMMARY_URL = (
    "https://www.95598.cn/osgweb01/electricityChargeQuery/queryElectricBillSummary"
)

# Home Assistant
SUPERVISOR_URL = "http://supervisor/core"
API_PATH = "/api/states/"

BALANCE_SENSOR_NAME = "sensor.electricity_charge_balance"
DAILY_USAGE_SENSOR_NAME = "sensor.last_electricity_usage"
YEARLY_USAGE_SENSOR_NAME = "sensor.yearly_electricity_usage"
YEARLY_CHARGE_SENSOR_NAME = "sensor.yearly_electricity_charge"
MONTH_USAGE_SENSOR_NAME = "sensor.month_electricity_usage"
LAST_MONTH_USAGE_SENSOR_NAME = "sensor.last_month_usage"
LAST_MONTH_CHARGE_SENSOR_NAME = "sensor.last_month_charge"
BALANCE_UNIT = "CNY"
USAGE_UNIT = "KWH"

# ── 统计类传感器：序列明细放 data 属性（数组），state 为合计值 ──
RECENT_30D_USAGE_SENSOR_NAME = "sensor.recent_30d_usage"
RECENT_30D_VALLEY_SENSOR_NAME = "sensor.recent_30d_valley_usage"
RECENT_30D_PEAK_SENSOR_NAME = "sensor.recent_30d_peak_usage"
RECENT_12M_CHARGE_SENSOR_NAME = "sensor.recent_12m_charge"
RECENT_12M_USAGE_SENSOR_NAME = "sensor.recent_12m_usage"
RECENT_12M_VALLEY_SENSOR_NAME = "sensor.recent_12m_valley_usage"
RECENT_12M_PEAK_SENSOR_NAME = "sensor.recent_12m_peak_usage"
RECENT_DAYS = 30        # 统计窗口：近 30 天
RECENT_MONTHS = 12      # 统计窗口：近 12 个月

# ── MQTT 设备：把一个户号的全部传感器归入同一个 Home Assistant 设备 ──
DEVICE_NAME = "国网电费"
DEVICE_MANUFACTURER = "ARC-MX"
DEVICE_MODEL = "sgcc_electricity_new"
DEVICE_SUPPORT_URL = "https://github.com/ARC-MX/sgcc_electricity_new"

LLM_API_KEY = os.getenv('LLM_API_KEY', '').strip()
LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://api.siliconflow.cn/v1')
LLM_MODEL = os.getenv('LLM_MODEL', 'Qwen/Qwen3.5-35B-A3B')


def get_data_dir() -> str:
    """获取数据存储目录：Docker 用 /data，本地用项目下的 data/"""
    if 'PYTHON_IN_DOCKER' in os.environ:
        return '/data'
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


DB_TYPE_ALIASES = ("sqlite", "mysql", "postgresql", "postgres", "postgre", "pg", "supabase")


def db_enabled() -> bool:
    """是否启用数据库存储（DB_TYPE 为 sqlite / mysql / postgresql 等）。"""
    return os.getenv("DB_TYPE", "None").strip().strip('\'" ').lower() in DB_TYPE_ALIASES


def is_multi_user() -> bool:
    """是否为多户号模式（MULTI_USER / ENABLE_MULTI_USER），默认 False 即单户号。"""
    raw = os.getenv("MULTI_USER", os.getenv("ENABLE_MULTI_USER", "false"))
    return str(raw).strip().strip('\'" ').lower() in ("1", "true", "yes", "y", "on")


def user_suffix(user_id: str) -> str:
    """数据库表名后缀：多户号为户号，单户号为空字符串。"""
    return str(user_id) if is_multi_user() else ""


def sensor_postfix(user_id: str) -> str:
    """传感器实体名后缀：多户号为 `_户号后4位`，单户号为空字符串。"""
    return f"_{str(user_id)[-4:]}" if is_multi_user() else ""

