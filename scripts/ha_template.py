"""根据 SensorUpdator 发布的传感器生成 Home Assistant 配置模板。

REST API 推送的实体在 HA 重启后会丢失，README 中要求手动编写 configuration.yaml
的 trigger 型 template 传感器来"固化"实体。本脚本按传感器定义自动生成这份配置，
新增 / 修改传感器时无需再手工维护 YAML。

用法：
    python ha_template.py                          # 单户号，打印到屏幕
    python ha_template.py --user-id 6400001234     # 多户号，实体名带 _1234 后缀
    python ha_template.py -o ha_template.yaml      # 写入文件
"""

import argparse
import os
import sys

from const import (BALANCE_SENSOR_NAME, DAILY_USAGE_SENSOR_NAME,
                   LAST_MONTH_CHARGE_SENSOR_NAME,
                   LAST_MONTH_USAGE_SENSOR_NAME, MONTH_USAGE_SENSOR_NAME,
                   RECENT_12M_CHARGE_SENSOR_NAME, RECENT_12M_PEAK_SENSOR_NAME,
                   RECENT_12M_USAGE_SENSOR_NAME,
                   RECENT_12M_VALLEY_SENSOR_NAME,
                   RECENT_30D_PEAK_SENSOR_NAME, RECENT_30D_USAGE_SENSOR_NAME,
                   RECENT_30D_VALLEY_SENSOR_NAME, YEARLY_CHARGE_SENSOR_NAME,
                   YEARLY_USAGE_SENSOR_NAME)
from sensor_updator import SensorUpdator

# 统计类传感器（近30天 / 近12个月）的公共属性：序列明细在 data 中
_SERIES_ATTRS = ("data", "count", "start", "end")

# (实体名, 友好名称, 单位, device_class, state_class, icon, 需要一并复制的属性)
# 与 sensor_updator.py 中各 update_* / _publish_series 写入的属性保持一致
SENSOR_SPECS = [
    (BALANCE_SENSOR_NAME, "电费余额", "CNY", "monetary", "total", "mdi:cash",
     ("last_reset", "amount_due")),
    (DAILY_USAGE_SENSOR_NAME, "最近一天用电量", "kWh", "energy", "measurement",
     "mdi:lightning-bolt", ("last_reset",)),
    (MONTH_USAGE_SENSOR_NAME, "当月用电量", "kWh", "energy", "measurement",
     "mdi:lightning-bolt", ("last_reset",)),
    (LAST_MONTH_USAGE_SENSOR_NAME, "上月用电量", "kWh", "energy", "measurement",
     "mdi:lightning-bolt", ("last_reset",)),
    (LAST_MONTH_CHARGE_SENSOR_NAME, "上月电费", "CNY", "monetary", "measurement",
     "mdi:cash", ("last_reset",)),
    (YEARLY_USAGE_SENSOR_NAME, "年度用电量", "kWh", "energy", "total_increasing",
     "mdi:lightning-bolt", ("last_reset",)),
    (YEARLY_CHARGE_SENSOR_NAME, "年度电费", "CNY", "monetary", "total_increasing",
     "mdi:cash", ("last_reset",)),
    (RECENT_30D_USAGE_SENSOR_NAME, "近30天电量", "kWh", "energy", "measurement",
     "mdi:lightning-bolt", _SERIES_ATTRS),
    (RECENT_30D_PEAK_SENSOR_NAME, "近30天峰值电量", "kWh", "energy", "measurement",
     "mdi:lightning-bolt", _SERIES_ATTRS),
    (RECENT_30D_VALLEY_SENSOR_NAME, "近30天峰谷电量", "kWh", "energy", "measurement",
     "mdi:lightning-bolt", _SERIES_ATTRS),
    (RECENT_12M_USAGE_SENSOR_NAME, "近12个月电量", "kWh", "energy", "measurement",
     "mdi:lightning-bolt", _SERIES_ATTRS),
    (RECENT_12M_CHARGE_SENSOR_NAME, "近12个月电费", "CNY", "monetary", "measurement",
     "mdi:cash", _SERIES_ATTRS),
    (RECENT_12M_PEAK_SENSOR_NAME, "近12个月峰值电量", "kWh", "energy", "measurement",
     "mdi:lightning-bolt", _SERIES_ATTRS),
    (RECENT_12M_VALLEY_SENSOR_NAME, "近12个月峰谷电量", "kWh", "energy", "measurement",
     "mdi:lightning-bolt", _SERIES_ATTRS),
]


def _object_id(sensor_name: str) -> str:
    """sensor.month_electricity_usage → month_electricity_usage"""
    return sensor_name.split(".", 1)[-1]


def render_sensor(spec, postfix: str) -> str:
    """生成一个 trigger 型 template 传感器配置块。"""
    entity, friendly, unit, device_class, state_class, icon, attrs = spec
    source = entity + postfix                      # 被转发的原始实体
    object_id = f"sgcc_{_object_id(entity)}{postfix}"  # 实体 id 统一带 sgcc 前缀
    name = SensorUpdator._friendly_name(friendly, postfix)  # 显示名（友好名称）

    lines = [
        "  - trigger:",
        "      - platform: event",
        "        event_type: state_changed",
        "        event_data:",
        f"          entity_id: {source}",
        "    sensor:",
        f"      - name: \"{name}\"",
        f"        default_entity_id: sensor.{object_id}",
        f"        unique_id: {object_id}",
        f"        state: \"{{{{ states('{source}') }}}}\"",
        f"        unit_of_measurement: \"{unit}\"",
        f"        device_class: {device_class}",
        f"        state_class: {state_class}",
        f"        icon: {icon}",
    ]
    if attrs:
        lines.append("        attributes:")
        for attr in attrs:
            lines.append(f"          {attr}: \"{{{{ state_attr('{source}', '{attr}') }}}}\"")
    return "\n".join(lines)


def render(user_id: str = "") -> str:
    """生成完整的 configuration.yaml 片段。"""
    postfix = f"_{str(user_id)[-4:]}" if user_id else ""
    header = [
        "# 由 scripts/ha_template.py 自动生成，粘贴到 configuration.yaml 后重启 Home Assistant",
        "# trigger 型 template：HA 重启后实体仍保留上次的值，解决 REST API 实体丢失的问题",
        "# name 即显示名（中文友好名），实体 id 由 default_entity_id 固定为 sensor.sgcc_*",
        f"# 户号: {user_id or '单户号模式（实体名不带后缀）'}",
        "template:",
    ]
    blocks = [render_sensor(spec, postfix) for spec in SENSOR_SPECS]
    return "\n".join(header + blocks) + "\n"


def main():
    parser = argparse.ArgumentParser(description="生成 Home Assistant 传感器配置模板")
    parser.add_argument("--user-id", default="", help="户号，多户号模式下实体名追加后 4 位")
    parser.add_argument("-o", "--output", default="", help="输出文件，缺省打印到屏幕")
    args = parser.parse_args()

    content = render(args.user_id)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"已生成模板: {os.path.abspath(args.output)}")
    else:
        sys.stdout.write(content)


if __name__ == "__main__":
    main()
