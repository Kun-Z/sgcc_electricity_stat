# 传感器属性说明

本文件说明程序推送到 Home Assistant 的全部传感器的 **state 含义** 与 **属性（attributes）**。
传感器由 `scripts/sensor_updator.py` 发布，实体名常量定义在 `scripts/const.py`。

## 1. 命名规则

- 单户号（默认）：实体名不带后缀，例如 `sensor.month_electricity_usage`；
- 多户号（`MULTI_USER=true`）：实体名追加户号后 4 位，例如 `sensor.month_electricity_usage_1234`，
  友好名称同时追加，如「电费余额 (1234)」；
- 用 `python scripts/ha_template.py` 生成的模板实体，entity_id 带 `sgcc` 前缀
  （如 `sensor.sgcc_month_electricity_usage`），显示名即下表的友好名称。

## 2. 通用属性

| 属性 | 说明 |
| --- | --- |
| `unit_of_measurement` | 单位：电量为 `kWh`，金额为 `CNY` |
| `device_class` | `energy`（电量）/ `monetary`（金额） |
| `state_class` | `measurement` / `total` / `total_increasing`，决定是否进入长期统计 |
| `icon` | `mdi:lightning-bolt`（电量）/ `mdi:cash`（金额） |
| `friendly_name` | 前端显示名，多户号时带户号后 4 位 |
| `last_reset` | 统计周期标识：日传感器为 `YYYY-MM-DD`，月传感器为 `YYYY-MM`，年传感器为 `YYYY`；电费余额为推送时刻 `YYYY-MM-DD, HH:MM:SS` |
| `data` | 仅统计类传感器（近30天 / 近12个月）有，逐日或逐月明细数组 |
| `count` / `start` / `end` | 仅统计类传感器有：条目数、区间起止（日期或月份） |
| `amount_due` | 仅电费余额有：应交金额（欠费金额），爬取到增强余额数据时才写入 |

> state 与上次相同（差值 ≤ 0.001）且关键属性未变化时**不会重复推送**，实体保持上一次的值。

## 3. 传感器一览

| 实体（单户号） | 友好名称 | 单位 / device_class / state_class | state 含义 | 附加属性 |
| --- | --- | --- | --- | --- |
| `sensor.electricity_charge_balance` | 电费余额 | CNY / monetary / total | 账户余额 | `last_reset`（推送时刻）、`amount_due` |
| `sensor.last_electricity_usage` | 最近一天用电量 | kWh / energy / measurement | 最近一日的用电量 | `last_reset`（该日日期） |
| `sensor.month_electricity_usage` | 当月用电量 | kWh / energy / measurement | 本月累计用电量 | `last_reset`（`YYYY-MM`） |
| `sensor.last_month_usage` | 上月用电量 | kWh / energy / measurement | 上月用电量 | `last_reset`（上月 `YYYY-MM`） |
| `sensor.last_month_charge` | 上月电费 | CNY / monetary / measurement | 上月电费 | `last_reset`（上月 `YYYY-MM`） |
| `sensor.yearly_electricity_usage` | 年度用电量 | kWh / energy / total_increasing | 年度累计用电量 | `last_reset`（年份，1 月时为上一年） |
| `sensor.yearly_electricity_charge` | 年度电费 | CNY / monetary / total_increasing | 年度累计电费 | `last_reset`（年份，1 月时为上一年） |
| `sensor.recent_30d_usage` | 近30天电量 | kWh / energy / measurement | 近 30 天每日用电量**合计** | `data`（按日）、`count`、`start`、`end` |
| `sensor.recent_30d_peak_usage` | 近30天峰值电量 | kWh / energy / measurement | 近 30 天每日峰时电量合计 | 同上 |
| `sensor.recent_30d_valley_usage` | 近30天峰谷电量 | kWh / energy / measurement | 近 30 天每日谷时电量合计 | 同上 |
| `sensor.recent_12m_usage` | 近12个月电量 | kWh / energy / measurement | 近 12 个月每月用电量合计 | `data`（按月）、`count`、`start`、`end` |
| `sensor.recent_12m_charge` | 近12个月电费 | CNY / monetary / measurement | 近 12 个月每月电费合计 | 同上 |
| `sensor.recent_12m_peak_usage` | 近12个月峰值电量 | kWh / energy / measurement | 近 12 个月每月峰时电量合计 | 同上 |
| `sensor.recent_12m_valley_usage` | 近12个月峰谷电量 | kWh / energy / measurement | 近 12 个月每月谷时电量合计 | 同上 |

## 4. 统计类传感器的 `data` 序列

`state` 为区间合计值（保留 2 位小数），逐日 / 逐月明细放在 `data` 属性里，避免 state 超过 255 字符被截断。

近 30 天（按日）：

```json
[
  {"date": "2026-09-06", "value": 6.52},
  {"date": "2026-09-07", "value": 7.13}
]
```

近 12 个月（按月）：

```json
[
  {"month": "2025-10", "value": 169.0},
  {"month": "2025-11", "value": 182.44}
]
```

- 数值均 `round(x, 2)`；
- **没有日期 / 月份或数值为空的记录不会出现在序列中**（例如某月无分时数据则该月缺失）；
- `count` 为序列条目数，`start` / `end` 为首尾记录的日期或月份。

## 5. 取值示例

取合计值：

```jinja
{{ states('sensor.recent_30d_usage') }}
```

取序列明细：

```jinja
{{ state_attr('sensor.recent_30d_usage', 'data') }}
```

apexcharts-card 画近 30 天柱状图：

```yaml
type: custom:apexcharts-card
graph_span: 30d
header:
  show: true
  title: 近30天用电量
series:
  - entity: sensor.recent_30d_usage
    type: column
    data_generator: |
      return entity.attributes.data.map((item) => {
        return [new Date(item.date).getTime(), item.value];
      });
```

## 6. 数据来源与注意事项

- 传感器数据**优先从数据库读取**（`DB_TYPE=sqlite / mysql / postgresql`，对应 `daily` / `monthly` / `yearly` / `data` 表），
  数据库中缺失的项用本次爬取的数据补齐；
- **未配置数据库时**回退到本次爬取结果，此时近30天 / 近12个月的历史序列可能不完整；
- 月表、年表的**峰值 / 峰谷电量没有官方数据**（网页不提供），由 `daily` 表按日汇总回填，
  数据库刚开始入库时统计会偏小，随时间累积逐步准确；
- 通过 REST API 推送的实体在 HA 重启后会丢失，需按 `python scripts/ha_template.py` 生成的
  trigger 型 template 配置固化（见 README 第 3 步）。
