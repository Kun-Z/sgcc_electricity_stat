import nest_asyncio; nest_asyncio.apply()
import logging
import logging.config
import os
import sys
import time
import schedule
import json
import random
from error_watcher import ErrorWatcher
from sensor_updator import SensorUpdator

from datetime import datetime,timedelta
from const import *
from data_fetcher import DataFetcher
from click_captcha_solver import LLMConfigError

def main():
    global RETRY_TIMES_LIMIT
    if 'PYTHON_IN_DOCKER' not in os.environ:
        # 读取 .env 文件
        import dotenv
        dotenv.load_dotenv(verbose=True)
    if os.path.isfile('/data/options.json'):
        with open('/data/options.json') as f:
            options = json.load(f)
        try:
            for key, value in options.items():
                os.environ[key] = str(value)
            import const
            const.LLM_API_KEY = os.getenv('LLM_API_KEY', '').strip()
            const.LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://api.siliconflow.cn/v1')
            const.LLM_MODEL = os.getenv('LLM_MODEL', 'Qwen/Qwen3.5-35B-A3B')
            logging.info(f"当前以Homeassistant Add-on 形式运行.")
        except Exception as e:
            logging.error(f"读取 options.json 文件失败，程序将退出，错误信息: {e}。")
            sys.exit()

    try:
        PHONE_NUMBER = os.getenv("PHONE_NUMBER")
        logging.info(f"读取环境变量 PHONE_NUMBER : {PHONE_NUMBER}")
        PASSWORD = os.getenv("PASSWORD")
        HASS_URL = os.getenv("HASS_URL")
        JOB_START_TIME = os.getenv("JOB_START_TIME","07:00" ).strip('"').strip("'")
        LOG_LEVEL = os.getenv("LOG_LEVEL","INFO")
        VERSION = os.getenv("VERSION")
        RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))

        logger_init(LOG_LEVEL)
        logging.info(f"当前以Docker镜像方式运行。")
    except Exception as e:
        logging.error(f"读取 .env 文件失败，程序将退出，错误信息: {e}。")
        sys.exit()

    logging.info(f"当前仓库版本为 {VERSION}，仓库地址为 https://github.com/ARC-MX/sgcc_electricity_new.git")
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"当前日期为 {current_datetime}。")

    logging.info(f"开始初始化 ErrorWatcher")
    ErrorWatcher.init(root_dir='/data/errors')
    logging.info(f'ErrorWatcher 初始化完成！')
    fetcher = DataFetcher(PHONE_NUMBER, PASSWORD)
    updator = SensorUpdator()

    # 生成随机延迟时间（-10分钟到+10分钟）
    random_delay_minutes = random.randint(-10, 10)
    parsed_time = datetime.strptime(JOB_START_TIME, "%H:%M") + timedelta(minutes=random_delay_minutes)
    logging.info(f"当前登录用户名为 {PHONE_NUMBER}，Home Assistant 地址为 {HASS_URL}，程序将每天在 {parsed_time.strftime('%H:%M')} 执行。")

    # 添加随机延迟
    next_run_time = parsed_time + timedelta(hours=12)

    logging.info(f'立即执行任务！下次运行时间为每天 {parsed_time.strftime("%H:%M")} 和 {next_run_time.strftime("%H:%M")}')
    schedule.every().day.at(parsed_time.strftime("%H:%M")).do(run_task, fetcher)
    schedule.every().day.at(next_run_time.strftime("%H:%M")).do(run_task, fetcher)

    # 重发缓存到 HA 的间隔（分钟），默认 60 分钟，用于防止 HA 重启后数据丢失
    CACHE_REPUBLISH_INTERVAL = int(os.getenv("CACHE_REPUBLISH_INTERVAL", "60"))
    schedule.every(CACHE_REPUBLISH_INTERVAL).minutes.do(republish_or_fetch, updator, fetcher)

    # 启动时先尝试从缓存恢复
    # 如果缓存恢复成功，则跳过本次启动时的实时抓取，避免频繁重启导致账号被封
    if not updator.republish():
        logging.info("未找到有效缓存，正在从国家电网获取数据...")
        try:
            run_task(fetcher)
        except Exception as e:
            logging.exception(f"启动时的抓取任务异常（已忽略，容器继续运行）: {e}")
    else:
        logging.info("已从缓存恢复数据，跳过启动时抓取以保护账号。")

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            # 单个任务异常绝不能终止调度主循环，否则容器会退出
            logging.exception(f"调度任务抛出异常（已忽略，继续运行）: {e}")
        time.sleep(1)


def republish_or_fetch(updator: SensorUpdator, fetcher: DataFetcher):
    if not updator.republish():
        logging.info("缓存数据已过期或不存在，正在从国家电网获取数据...")
        run_task(fetcher)


def run_task(data_fetcher: DataFetcher):
    """执行一轮抓取任务。

    任何异常都不得导致进程退出：容器需要常驻，等待下一个调度周期自动重试。
    """
    for retry_times in range(1, RETRY_TIMES_LIMIT + 1):
        try:
            data_fetcher.fetch()
            return
        except LLMConfigError as e:
            # 配置类错误重试无意义，放弃本轮，容器继续运行等待下次调度
            logging.error(
                f"LLM 配置错误，放弃本轮任务（容器保持运行，请检查 LLM_API_KEY/LLM_MODEL/LLM_BASE_URL）: {e}")
            return
        except Exception as e:
            remaining = RETRY_TIMES_LIMIT - retry_times
            logging.error(f"状态刷新任务失败，原因是 [{e}]，还剩 {remaining} 次重试机会。")
            if remaining > 0:
                # 退避重试，避免账号被风控
                time.sleep(min(60 * retry_times, 300) + random.uniform(0, 30))
            continue
    logging.error(f"本轮任务在 {RETRY_TIMES_LIMIT} 次重试后仍失败，保持运行并等待下一个调度周期。")

def logger_init(level: str):
    logger = logging.getLogger()
    logger.setLevel(level.strip().strip('\'" '))  # 兼容 .env 中带引号和空格的 LOG_LEVEL
    # 清除已有 handler（避免 basicConfig 自动添加的默认 handler 导致日志重复）
    logger.handlers.clear()
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)
    format = logging.Formatter("%(asctime)s  [%(levelname)-8s] ---- %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setFormatter(format)
    logger.addHandler(sh)


if __name__ == "__main__":
    main()
