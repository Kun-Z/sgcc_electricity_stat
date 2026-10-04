import json
import logging
import os
from datetime import datetime, timedelta

import requests
from const import *


class SensorUpdator:

    def __init__(self):
        HASS_URL = os.getenv("HASS_URL")
        HASS_TOKEN = os.getenv("HASS_TOKEN")
        self.base_url = HASS_URL[:-1] if HASS_URL.endswith("/") else HASS_URL
        self.token = HASS_TOKEN
        self._mqtt = None        # 当前户号的 MQTT 设备（未启用 MQTT 时为 None）
        self._init_balance_notify()

    # ── MQTT 设备（把传感器归入同一个设备），未启用时回退 REST API ──

    def _open_mqtt_device(self, user_id: str):
        try:
            from mqtt_device import MqttDevice
            if not MqttDevice.enabled():
                return None
            device = MqttDevice(user_id)
            logging.info(f"[{user_id}] 使用 MQTT 设备模式发布传感器")
            return device
        except Exception as e:
            logging.warning(f"[{user_id}] MQTT 设备初始化失败，回退 REST API: {e}")
            return None

    def _close_mqtt_device(self):
        if self._mqtt is not None:
            self._mqtt.close()
            self._mqtt = None

    @staticmethod
    def _friendly_name(base: str, postfix: str) -> str:
        """友好名称；多户号时追加户号后 4 位以作区分。"""
        return f"{base} ({postfix.lstrip('_')})" if postfix else base

    @staticmethod
    def _to_float(value, default=None):
        """安全转 float，无法转换时返回默认值。"""
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _publish(self, sensorName: str, sensorState, attributes: dict):
        """统一发布出口：MQTT 优先，失败或未启用则走 REST API。"""
        if self._mqtt is not None and self._mqtt.publish(sensorName, sensorState, attributes):
            return
        self.send_url(sensorName, {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": attributes,
        })

    def _init_balance_notify(self):
        push_type = os.getenv("PUSH_TYPE", "None").lower()
        if push_type == "pushplus":
            from notify import PushplusNotify
            self.balance_notify = PushplusNotify()
        elif push_type == "urlpush":
            from notify import UrlPushNotify
            self.balance_notify = UrlPushNotify()
        else:
            self.balance_notify = None


    def update_one_userid(self, user_id: str, balance: float = None, last_daily_date: str = None, last_daily_usage: float = None, yearly_charge: float = None, yearly_usage: float = None, month_charge: float = None, month_usage: float = None, tou_data: dict = None, enhanced_balance: dict = None, bill_tou_data: dict = None, months_data: list = None, notify=True):
        """更新一个户号的全部传感器。数据统一从数据库读取，数据库不可用时回退到本次爬取的数据。"""
        logging.info(f"[{user_id}] 开始更新 Home Assistant 传感器数据...")
        self._save_to_cache(user_id, balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data, enhanced_balance, bill_tou_data, months_data)
        postfix = sensor_postfix(user_id)
        self._mqtt = self._open_mqtt_device(user_id)
        db = self._open_db(user_id)
        try:
            stats = self._build_stats(db, {
                "balance": balance,
                "last_daily_date": last_daily_date,
                "last_daily_usage": last_daily_usage,
                "yearly_charge": yearly_charge,
                "yearly_usage": yearly_usage,
                "month_usage": month_usage,
                "tou_data": tou_data,
                "enhanced_balance": enhanced_balance,
                "months_data": months_data,
            })
            self._publish_stats(postfix, stats, user_id=user_id, notify=notify)
            logging.info(f"[{user_id}] Home Assistant 传感器数据更新完成!")
        finally:
            if db is not None:
                db.close_connect()
            self._close_mqtt_device()

    # ── 数据来源：数据库优先，爬取数据兜底 ──

    def _open_db(self, user_id: str):
        """传感器统一从数据库取数；未配置数据库时返回 None。"""
        if not db_enabled():
            logging.info(f"[{user_id}] 未配置数据库，传感器数据回退到本次爬取结果。")
            return None
        try:
            from db import create_db
            db = create_db(os.getenv("DB_TYPE", "sqlite"))
            if db.connect_user_db(user_id):
                return db
            db.close_connect()
        except Exception as e:
            logging.warning(f"[{user_id}] 数据库连接失败，传感器数据回退到本次爬取结果: {e}")
        return None

    @staticmethod
    def _month_stats(row: dict) -> dict:
        """月度行 → {month, usage, charge}，无有效数据时返回 {}。"""
        if not row:
            return {}
        month = str(row.get("month") or "").strip()
        if not month:
            return {}
        return {
            "month": month,
            "usage": SensorUpdator._to_float(row.get("usage")),
            "charge": SensorUpdator._to_float(row.get("charge")),
        }

    def _stats_from_db(self, db) -> dict:
        """从数据库的 daily / monthly / yearly / data 表读取统计视图。"""
        stats = {"daily": [], "monthly": [], "yearly": {}, "balance": {},
                 "last": None, "current_month": {}, "last_month": {}, "current_year": {}}
        try:
            daily = db.get_daily_data(RECENT_DAYS)
            monthly = db.get_monthly_data(RECENT_MONTHS)
            all_months = db.get_monthly_data(0)
            now = datetime.now()
            cur_key = now.strftime("%Y-%m")
            prev_key = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

            by_month = {str(m.get("month") or "").strip(): m for m in all_months}
            current = by_month.get(cur_key)
            previous = by_month.get(prev_key)
            if previous is None:
                # 没有上月数据时，取当月之前最近的一个月
                earlier = [m for m in all_months
                           if str(m.get("month") or "").strip() < cur_key]
                previous = earlier[-1] if earlier else None
            yearly = db.get_yearly_data(str(now.year)) or db.get_yearly_data()

            stats["daily"] = daily
            stats["monthly"] = monthly
            stats["yearly"] = yearly or {}
            stats["balance"] = db.get_latest_balance() or {}
            stats["current_month"] = self._month_stats(current)
            stats["last_month"] = self._month_stats(previous)
            stats["current_year"] = {
                "year": str(yearly.get("year") or "") if yearly else "",
                "usage": self._to_float((yearly or {}).get("usage")),
                "charge": self._to_float((yearly or {}).get("charge")),
            }
            if daily:
                last_row = daily[-1]
                stats["last"] = {
                    "date": str(last_row.get("date") or ""),
                    "usage": self._to_float(last_row.get("usage")),
                }
            logging.info(f"[数据库] 日数据 {len(daily)} 条, 月数据 {len(monthly)} 条")
        except Exception as e:
            logging.error(f"从数据库读取统计数据失败: {e}")
        return stats

    def _stats_from_fetch(self, values: dict) -> dict:
        """数据库不可用时的兜底：用本次爬取的数据拼出同样的统计视图。"""
        tou_data = values.get("tou_data") or {}
        daily = []
        for row in tou_data.get("daily", []):
            date = str(row.get("date") or "").strip()
            if not date:
                continue
            daily.append({
                "date": date,
                "usage": self._to_float(row.get("total_usage")),
                "peak_usage": self._to_float(row.get("peak_usage")),
                "valley_usage": self._to_float(row.get("valley_usage")),
            })
        daily.sort(key=lambda r: r["date"])
        daily = daily[-RECENT_DAYS:]

        months = []
        for row in values.get("months_data") or []:
            month = str(row.get("month") or "").strip()
            if not month:
                continue
            months.append({
                "month": month,
                "usage": self._to_float(row.get("usage")),
                "charge": self._to_float(row.get("charge")),
                "peak_usage": None,
                "valley_usage": None,
            })
        months.sort(key=lambda r: r["month"])
        # 峰值 / 峰谷：同样按日数据汇总到月
        for row in daily:
            if len(row["date"]) < 7:
                continue
            target = next((m for m in months if m["month"] == row["date"][:7]), None)
            if target is None:
                target = {"month": row["date"][:7], "usage": None, "charge": None,
                          "peak_usage": 0.0, "valley_usage": 0.0}
                months.append(target)
            target["peak_usage"] = (target["peak_usage"] or 0.0) + (row.get("peak_usage") or 0.0)
            target["valley_usage"] = (target["valley_usage"] or 0.0) + (row.get("valley_usage") or 0.0)
        months.sort(key=lambda r: r["month"])

        now = datetime.now()
        cur_key = now.strftime("%Y-%m")
        prev_key = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        enhanced = values.get("enhanced_balance") or {}
        return {
            "daily": daily,
            "monthly": months[-RECENT_MONTHS:],
            "balance": {
                "balance": self._to_float(values.get("balance")),
                "amount_due": self._to_float(enhanced.get("amount_due")),
            },
            "last": {
                "date": values.get("last_daily_date") or "",
                "usage": self._to_float(values.get("last_daily_usage")),
            } if values.get("last_daily_usage") is not None else None,
            "current_month": {"month": cur_key,
                              "usage": self._to_float(values.get("month_usage")),
                              "charge": None},
            "last_month": self._month_stats(
                next((m for m in months if m["month"] == prev_key), None)),
            "current_year": {
                "year": str(now.year),
                "usage": self._to_float(values.get("yearly_usage")),
                "charge": self._to_float(values.get("yearly_charge")),
            },
        }

    def _build_stats(self, db, values: dict) -> dict:
        """统一数据视图：数据库优先，缺失项用本次爬取的数据补齐。"""
        stats = self._stats_from_db(db) if db is not None else {}
        if not stats:
            stats = {"daily": [], "monthly": [], "balance": {}, "last": None,
                     "current_month": {}, "last_month": {}, "current_year": {}}
        fallback = self._stats_from_fetch(values)
        for key in ("balance", "last", "current_month", "last_month", "current_year"):
            current = stats.get(key)
            if not current or (isinstance(current, dict)
                               and all(v is None for v in current.values())):
                stats[key] = fallback.get(key)
        for key in ("daily", "monthly"):
            if not stats.get(key):
                stats[key] = fallback.get(key)
        return stats

    def _publish_stats(self, postfix: str, stats: dict, user_id: str = "", notify=True):
        """按统一数据视图发布传感器。"""
        balance = stats.get("balance") or {}
        if balance.get("balance") is not None:
            if notify and self.balance_notify is not None:
                self.balance_notify(user_id, balance["balance"])
            self.update_balance(postfix, balance["balance"], balance.get("amount_due"))

        last = stats.get("last") or {}
        if last.get("usage") is not None:
            self.update_last_daily_usage(postfix, last.get("date", ""), last["usage"])

        current_month = stats.get("current_month") or {}
        if current_month.get("usage") is not None:
            self.update_month_data(postfix, current_month["usage"], current_month.get("month"))

        last_month = stats.get("last_month") or {}
        if last_month.get("usage") is not None:
            self.update_last_month_data(postfix, last_month["usage"], last_month.get("month"), usage=True)
        if last_month.get("charge") is not None:
            self.update_last_month_data(postfix, last_month["charge"], last_month.get("month"), usage=False)

        current_year = stats.get("current_year") or {}
        if current_year.get("usage") is not None:
            self.update_yearly_data(postfix, current_year["usage"], usage=True)
        if current_year.get("charge") is not None:
            self.update_yearly_data(postfix, current_year["charge"])

        # 统计类传感器（近30天 / 近12个月）
        self._update_stat_sensors(postfix, stats.get("daily") or [], stats.get("monthly") or [])

    def _get_cache_file(self):
        from const import get_data_dir
        return os.path.join(get_data_dir(), 'sgcc_cache.json')

    def _save_to_cache(self, user_id, balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data=None, enhanced_balance=None, bill_tou_data=None, months_data=None):
        cache_file = self._get_cache_file()
        abs_cache_file = os.path.abspath(cache_file)
        data = {}
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = json.load(f)
        except Exception as e:
            logging.warning(f"加载缓存文件失败: {e}")

        cache_entry = {
            "balance": balance,
            "last_daily_date": last_daily_date,
            "last_daily_usage": last_daily_usage,
            "yearly_charge": yearly_charge,
            "yearly_usage": yearly_usage,
            "month_charge": month_charge,
            "month_usage": month_usage,
            "timestamp": datetime.now().isoformat()
        }

        if tou_data:
            cache_entry["tou_data"] = tou_data
        if enhanced_balance:
            cache_entry["enhanced_balance"] = enhanced_balance
        if bill_tou_data:
            cache_entry["bill_tou_data"] = bill_tou_data
        if months_data:
            cache_entry["months_data"] = months_data

        data[user_id] = cache_entry

        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.debug(f"已保存数据到缓存文件: {abs_cache_file}")
        except Exception as e:
            logging.error(f"保存缓存文件失败 {abs_cache_file}: {e}")

    def republish(self):
        cache_file = self._get_cache_file()
        abs_cache_file = os.path.abspath(cache_file)
        if not os.path.exists(cache_file):
            logging.info(f"未找到缓存文件 {abs_cache_file}，跳过重新推送。")
            return False

        data = {}
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
        except Exception as e:
            logging.error(f"加载缓存文件失败 {abs_cache_file}: {e}")
            return False

        # 检查缓存数据的日期是否与当前日期一致
        today_str = datetime.now().strftime("%Y-%m-%d")
        for user_id, values in data.items():
            cache_timestamp = values.get("timestamp", "")
            cache_date = cache_timestamp[:10] if cache_timestamp else ""
            if cache_date != today_str:
                logging.info(f"缓存数据日期({cache_date})与当前日期({today_str})不一致，需要从国家电网重新获取数据。")
                return False

        try:
            for user_id, values in data.items():
                logging.info(f"正在从缓存重新推送用户 {user_id} 的数据。")
                clean_values = {k: v for k, v in values.items() if k != 'timestamp'}
                self.update_one_userid(user_id, **clean_values, notify=False)
            return True
        except Exception as e:
            logging.error(f"重新推送数据失败: {e}")
            return False

    def get_sensor_state(self, sensor_name):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.token,
        }
        url = self.base_url + API_PATH + sensor_name
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logging.warning(f"获取传感器 {sensor_name} 状态失败: {e}")
            return None

    def should_update(self, sensor_name, new_state, check_attributes=None):
        current_state_obj = self.get_sensor_state(sensor_name)
        if not current_state_obj:
            return True

        # 检查状态
        try:
            current_state = current_state_obj.get('state')
            if current_state in ['unknown', 'unavailable', None]:
                return True

            curr_val = float(current_state)
            new_val = float(new_state)
            if abs(curr_val - new_val) > 0.001:
                return True
        except (ValueError, TypeError):
            # 如果无法作为浮点数比较，则假定不同
            return True

        # 如需则检查属性
        if check_attributes:
            curr_attrs = current_state_obj.get('attributes', {})
            for k, v in check_attributes.items():
                # 转换为字符串进行比较以避免类型不匹配
                if str(curr_attrs.get(k)) != str(v):
                    return True

        return False

    def update_last_daily_usage(self, postfix: str, last_daily_date: str, sensorState: float):
        sensorName = DAILY_USAGE_SENSOR_NAME + postfix

        if not self.should_update(sensorName, sensorState, {"last_reset": last_daily_date}):
             logging.info(f"跳过 {sensorName} 的更新，状态相同。")
             return

        attributes = {
            "last_reset": last_daily_date,
            "unit_of_measurement": "kWh",
            "icon": "mdi:lightning-bolt",
            "device_class": "energy",
            "state_class": "measurement",
            "friendly_name": self._friendly_name("最近一天用电量", postfix),
        }

        self._publish(sensorName, sensorState, attributes)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} kWh")

    def update_balance(self, postfix: str, sensorState: float, amount_due: float = None):
        sensorName = BALANCE_SENSOR_NAME + postfix

        if not self.should_update(sensorName, sensorState):
             logging.info(f"跳过 {sensorName} 的更新，状态相同。")
             return

        last_reset = datetime.now().strftime("%Y-%m-%d, %H:%M:%S")
        attributes = {
            "last_reset": last_reset,
            "unit_of_measurement": "CNY",
            "icon": "mdi:cash",
            "device_class": "monetary",
            "state_class": "total",
            "friendly_name": self._friendly_name("电费余额", postfix),
        }
        if amount_due is not None:
            attributes["amount_due"] = amount_due

        self._publish(sensorName, sensorState, attributes)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} CNY")

    def update_month_data(self, postfix: str, sensorState: float, month: str = None):
        """当月用电量"""
        sensorName = MONTH_USAGE_SENSOR_NAME + postfix
        last_reset = month or datetime.now().strftime("%Y-%m")

        if not self.should_update(sensorName, sensorState, {"last_reset": last_reset}):
             logging.info(f"跳过 {sensorName} 的更新，状态相同。")
             return

        attributes = {
            "last_reset": last_reset,
            "unit_of_measurement": "kWh",
            "icon": "mdi:lightning-bolt",
            "device_class": "energy",
            "state_class": "measurement",
            "friendly_name": self._friendly_name("当月用电量", postfix),
        }

        self._publish(sensorName, sensorState, attributes)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} kWh")

    def update_last_month_data(self, postfix: str, sensorState: float, month: str = None, usage=True):
        """上月用电量 / 上月电费"""
        sensorName = (
            LAST_MONTH_USAGE_SENSOR_NAME + postfix
            if usage
            else LAST_MONTH_CHARGE_SENSOR_NAME + postfix
        )
        if not month:
            last_day_of_previous_month = datetime.now().replace(day=1) - timedelta(days=1)
            month = last_day_of_previous_month.strftime("%Y-%m")

        if not self.should_update(sensorName, sensorState, {"last_reset": month}):
             logging.info(f"跳过 {sensorName} 的更新，状态相同。")
             return

        attributes = {
            "last_reset": month,
            "unit_of_measurement": "kWh" if usage else "CNY",
            "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
            "device_class": "energy" if usage else "monetary",
            "state_class": "measurement",
            "friendly_name": self._friendly_name(
                "上月用电量" if usage else "上月电费", postfix),
        }
        self._publish(sensorName, sensorState, attributes)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} {'kWh' if usage else 'CNY'}")

    def update_yearly_data(self, postfix: str, sensorState: float, usage=False):
        sensorName = (
            YEARLY_USAGE_SENSOR_NAME + postfix
            if usage
            else YEARLY_CHARGE_SENSOR_NAME + postfix
        )
        if datetime.now().month == 1:
            last_year = datetime.now().year -1
            last_reset = datetime.now().replace(year=last_year).strftime("%Y")
        else:
            last_reset = datetime.now().strftime("%Y")

        if not self.should_update(sensorName, sensorState, {"last_reset": last_reset}):
             logging.info(f"跳过 {sensorName} 的更新，状态相同。")
             return

        attributes = {
            "last_reset": last_reset,
            "unit_of_measurement": "kWh" if usage else "CNY",
            "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
            "device_class": "energy" if usage else "monetary",
            "state_class": "total_increasing",
            "friendly_name": self._friendly_name(
                "年度用电量" if usage else "年度电费", postfix),
        }
        self._publish(sensorName, sensorState, attributes)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} {'kWh' if usage else 'CNY'}")

    # ── 统计类传感器 ──

    @staticmethod
    def _build_series(rows: list, key: str, field: str) -> list:
        """把记录转成 [{key, value}] 序列；缺少日期/月份或数值的记录直接跳过。"""
        series = []
        for row in rows or []:
            label = str(row.get(key, "") or "").strip()
            value = SensorUpdator._to_float(row.get(field))
            if not label or value is None:
                continue
            series.append({key: label, "value": round(value, 2)})
        return series

    def _publish_series(self, sensorName: str, friendly: str, series: list,
                        key: str, unit: str, device_class: str, icon: str):
        """统计类传感器：state 为合计值，明细序列放 data 属性（避免 state 超 255 字符被截断）。"""
        if not series:
            logging.info(f"{sensorName} 无有效数据，跳过更新。")
            return

        total = round(sum(item["value"] for item in series), 2)
        attributes = {
            "unit_of_measurement": unit,
            "icon": icon,
            "device_class": device_class,
            "state_class": "measurement",
            "friendly_name": friendly,
            "data": series,
            "count": len(series),
            "start": series[0][key],
            "end": series[-1][key],
        }
        if not self.should_update(sensorName, total, {"count": len(series), "end": series[-1][key]}):
            logging.info(f"跳过 {sensorName} 的更新，状态相同。")
            return

        self._publish(sensorName, total, attributes)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {total} {unit} ({len(series)} 条)")

    def _update_stat_sensors(self, postfix: str, daily_rows: list, monthly_rows: list):
        """近30天电量/峰值/峰谷、近12个月电量/电费/峰值/峰谷。

        数据来自数据库（daily / monthly 表）；序列明细放在 data 属性，
        没有日期或月份、没有数值的记录不会出现在序列中。
        """
        daily_rows = sorted((r for r in daily_rows if r.get("date")),
                            key=lambda r: str(r.get("date")))[-RECENT_DAYS:]
        monthly_rows = sorted((r for r in monthly_rows if r.get("month")),
                              key=lambda r: str(r.get("month")))[-RECENT_MONTHS:]

        # ── 近30天：电量 / 峰值 / 峰谷 ──
        for sensor_base, field, friendly in (
            (RECENT_30D_USAGE_SENSOR_NAME, "usage", "近30天电量"),
            (RECENT_30D_PEAK_SENSOR_NAME, "peak_usage", "近30天峰值电量"),
            (RECENT_30D_VALLEY_SENSOR_NAME, "valley_usage", "近30天峰谷电量"),
        ):
            self._publish_series(
                sensor_base + postfix, self._friendly_name(friendly, postfix),
                self._build_series(daily_rows, "date", field),
                "date", "kWh", "energy", "mdi:lightning-bolt")

        # ── 近12个月：电量 / 电费 / 峰值 / 峰谷 ──
        for sensor_base, field, friendly, unit, device_class, icon in (
            (RECENT_12M_USAGE_SENSOR_NAME, "usage", "近12个月电量", "kWh", "energy", "mdi:lightning-bolt"),
            (RECENT_12M_CHARGE_SENSOR_NAME, "charge", "近12个月电费", "CNY", "monetary", "mdi:cash"),
            (RECENT_12M_PEAK_SENSOR_NAME, "peak_usage", "近12个月峰值电量", "kWh", "energy", "mdi:lightning-bolt"),
            (RECENT_12M_VALLEY_SENSOR_NAME, "valley_usage", "近12个月峰谷电量", "kWh", "energy", "mdi:lightning-bolt"),
        ):
            self._publish_series(
                sensor_base + postfix, self._friendly_name(friendly, postfix),
                self._build_series(monthly_rows, "month", field),
                "month", unit, device_class, icon)

    def send_url(self, sensorName, request_body):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.token,
        }
        url = self.base_url + API_PATH + sensorName
        try:
            response = requests.post(url, json=request_body, headers=headers, timeout=15)
            logging.debug(
                f"Home Assistant REST API 调用，POST {url}。响应[{response.status_code}]: {response.content}"
            )
        except Exception as e:
            logging.error(f"Home Assistant REST API 调用失败，原因是 {e}")
