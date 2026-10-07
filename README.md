## 简介

本应用可以帮助你将国网的电费、用电量数据接入homeassistant，实现实时追踪家庭用电量情况；并且可以将每日用电量保存到数据库，历史有迹可循。具体提供两类数据：

1. 在homeassistant以实体显示：

   | 实体entity_id                          | 友好名称 friendly_name | 说明                                               |
   | -------------------------------------- | ---------------------- | -------------------------------------------------- |
   | sensor.electricity_charge_balance_xxxx | 电费余额               | 电费余额，单位元（属性 `amount_due` 为应交金额）。 |
   | sensor.last_electricity_usage_xxxx     | 最近一天用电量         | 最近一天用电量，单位KWH、度。                      |
   | sensor.month_electricity_usage_xxxx    | 当月用电量             | 当月用电量，单位KWH、度。                          |
   | sensor.last_month_usage_xxxx           | 上月用电量             | 上月用电量，单位KWH、度。                          |
   | sensor.last_month_charge_xxxx          | 上月电费               | 上月电费，单位元。                                 |
   | sensor.yearly_electricity_usage_xxxx   | 今年用电量             | 今年总用电量，单位KWH、度。                        |
   | sensor.yearly_electricity_charge_xxxx  | 今年电费               | 今年总用电费，单位元。                             |
   | sensor.recent_30d_usage_xxxx           | 近30天电量             | 近30天每日用电量合计，单位KWH。                    |
   | sensor.recent_30d_peak_usage_xxxx      | 近30天峰值电量         | 近30天每日峰时电量合计，单位KWH。                  |
   | sensor.recent_30d_valley_usage_xxxx    | 近30天峰谷电量         | 近30天每日谷时电量合计，单位KWH。                  |
   | sensor.recent_12m_usage_xxxx           | 近12个月电量           | 近12个月每月用电量合计，单位KWH。                  |
   | sensor.recent_12m_charge_xxxx          | 近12个月电费           | 近12个月每月电费合计，单位元。                     |
   | sensor.recent_12m_peak_usage_xxxx      | 近12个月峰值电量       | 近12个月每月峰时电量合计，单位KWH。                |
   | sensor.recent_12m_valley_usage_xxxx    | 近12个月峰谷电量       | 近12个月每月谷时电量合计，单位KWH。                |

   > 实体名末尾的 `_xxxx` 为户号后 4 位，仅在**多户号模式**（`MULTI_USER=true`）下添加；
   > 默认单户号模式实体名不带后缀，例如 `sensor.month_electricity_usage`；
   > 多户号模式下 friendly_name 也会追加户号后 4 位，如「电费余额 (0123)」。

   > **数据统一从数据库读取**（`DB_TYPE=sqlite/mysql/postgresql`）；
   > 未配置数据库时自动回退到本次爬取的实时数据（近30天/近12个月等历史序列不可用）。

   可选：配置 MQTT 后，这些传感器会自动归入同一个「国网电费」设备，详见下方
   [传感器归入同一设备](#传感器归入同一设备可选)。

### 统计类传感器（近30天 / 近12个月）

`sensor.recent_30d_*` 与 `sensor.recent_12m_*` 的 **state 为区间合计值**，
逐日 / 逐月明细放在 `data` 属性（数组）中，避免 state 超过 255 字符被截断：

```yaml
# data 属性示例
- date: "2026-10-01"    # 月度传感器为 month: "2026-10"
  value: 12.34
- date: "2026-10-02"
  value: 10.02
```

同时附带 `count`（条目数）、`start`、`end`（区间起止）。
**没有日期/月份或没有数值的记录不会出现在序列中**（例如某月无分时数据则该月缺失）。

取数组示例（模板 / apexcharts-card）：

```jinja
{{ state_attr('sensor.recent_30d_usage', 'data') }}
```

> 说明：月表 / 年表的**峰值、峰谷电量没有官方数据（Vue 页面不提供）**，
> 由 `daily` 表按日汇总回填。数据库刚开始入库时统计会偏小，随时间累积逐步准确。

### 数据入库流程

一次抓取的写入过程：

1. `daily` 表：写入近 7~30 天的**用电量、峰值电量、峰谷电量**（按日期 upsert，已有日期更新）；
2. `monthly` 表：写入每月**用电量、电费**（按月份 upsert）；
3. `yearly` 表：写入今年**用电量、电费**（按年份 upsert）；
4. 汇总回填：由 `daily` 表按月 / 按年统计**峰值、峰谷电量**，写回 `monthly` / `yearly` 表；
5. 传感器发布：全部从上述三张表（及 `data` 表中的余额）读取。

2. 可选，近三十天每日用电量数据（SQLite数据库）

   共四张表（SQLite / MySQL / PostgreSQL 结构一致）：

   | 表名                                    | 说明                                                       |
   | --------------------------------------- | ---------------------------------------------------------- |
   | `daily{户号}`                           | 每日数据：`date`(主键)、`usage`、`peak_usage`、`valley_usage` |
   | `monthly{户号}`                         | 每月数据：`month`(主键)、`charge`、`usage`、`peak_usage`、`valley_usage` |
   | `yearly{户号}`                          | 每年数据：`year`(主键)、`charge`、`usage`、`peak_usage`、`valley_usage` |
   | `data{户号}`                            | 扩展数据：`name`(主键)、`value`（用户信息、余额日志等）     |

   默认单户号模式下表名不带户号后缀，即 `daily` / `monthly` / `yearly` / `data`；
   多户号模式（`MULTI_USER=true`）下表名带户号后缀，如 `daily1234567890123`。

   在项目路径下有个homeassistant.db  的数据库文件就是；
   如需查询可以用

   ```
   "SELECT * FROM daily;"
   "SELECT * FROM monthly;"
   "SELECT * FROM yearly;"
   ```

   得到如下结果：

<img src="assets/database.png" alt="mini-graph-card" width="400">

## 传感器归入同一设备（可选）

默认通过 Home Assistant REST API 推送状态，这种方式创建的实体**不属于任何设备**。
如果希望在「设置 → 设备与服务」中看到统一的设备卡片（一个户号 = 一个设备，其下挂载全部传感器），
可以配置 MQTT，程序会通过 **MQTT 发现**自动注册设备与实体：

```bash
MQTT_HOST="192.168.31.155"     # 留空则不使用 MQTT，回退 REST API
MQTT_PORT=1883
MQTT_USERNAME=""               # 可选
MQTT_PASSWORD=""               # 可选
MQTT_DISCOVERY_PREFIX="homeassistant"   # 需与 HA 的 MQTT 发现前缀一致
```

前提条件：

1. Home Assistant 已安装并配置 **MQTT 集成**（需有 MQTT Broker，如 Mosquitto）；
2. HA 的 MQTT 发现前缀与 `MQTT_DISCOVERY_PREFIX` 一致（默认 `homeassistant`）。

效果：

- 设备名为「国网电费」（多户号模式下为「国网电费 0123」，一个户号一个设备）；
- 设备下包含该户号的电量、电费、峰谷等全部传感器；
- 发现消息中使用 `default_entity_id`，会**保持原有 entity_id 不变**（如 `sensor.month_electricity_usage`），
  已有的卡片 / 自动化无需改动（需要 HA 2023.8 及以上版本）；
- MQTT 连接或发布失败时会自动回退到 REST API，不会丢数据。

## 适用范围

1. 适用于除南方电网覆盖省份外的用户。即除广东、广西、云南、贵州、海南等省份的用户外，均可使用本应用获取电力、电费数据。
2. 不管是通过哪种哪种安装的homeassistant，只要可以运行python，有约1G硬盘空间和500M运行内存，都可以采用本仓库部署。

本镜像支持架构：

> - `linux/amd64`：适用于 x86-64（amd64）架构的 Linux 系统，例如windows电脑。
> - `linux/arm64`：适用于 ARMv8 架构的 Linux 系统，例如树莓派3+，N1盒子等。
> - `linux/armv7`，暂不提供 ARMv7 架构的 Linux 系统，例如树莓派2，玩客云等，主要原因是onnx-runtime没有armv7版本的库，用户可以参考 [https://github.com/nknytk/built-onnxruntime-for-raspberrypi-linux.git](https://github.com/nknytk/built-onnxruntime-for-raspberrypi-linux.git)自行安装库然后编译docker镜像。

## 实现流程

通过 Python 的 **Playwright** 自动化获取国家电网官网的电费电量数据，绕过网站的反爬虫检测。

登录支持两种模式，验证码均由**大模型（LLM）视觉识别**自动解算：

| 登录方式                        | 验证码类型                   | LLM 方案                                | 适用场景 |
| ------------------------------- | ---------------------------- | --------------------------------------- | -------- |
| 密码登录                        | 腾讯**图标点击**验证码 | DOM 提取参考图标 + 主图 → 图标匹配定位 | 生产环境 |
| 短信验证码登录 (`DEBUG_MODE`) | 腾讯**文字顺序**验证码 | 截图 + LLM 读提示文字 → 按顺序点击汉字 | 本机调试 |

Cookie 自动持久化到 `data/sgcc_cookies.json`，下次启动时复用，避免频繁登录触发风控。数据获取优先使用页面 **Vue 状态注入**（一次性提取年度/月度/日分时数据），DOM 方式兜底。获取数据后通过 Home Assistant 的 [REST API](https://developers.home-assistant.io/docs/api/rest/) 将实体状态 POST 更新到 Home Assistant。

# 安装与部署

## 0）获取大模型 API Key（必读）

本项目使用**硅基流动（SiliconFlow）** 的 **Qwen/Qwen3.5-35B-A3B** 多模态大模型自动解算国家电网验证码。支持任意 OpenAI 兼容 API。

### 注册步骤

> **🎁 推荐链接：https://cloud.siliconflow.cn/i/2JCjsjdB（邀请码 `2JCjsjdB`），点击注册即赠 16元代金券（充值一分钱后可使用）！**

<p align="center">
<img src="assets/share_sf-2JCjsjdB.png" alt="硅基流动注册二维码" width="250">
</p>

1. 注册 [硅基流动账号](https://cloud.siliconflow.cn/i/2JCjsjdB)：`https://cloud.siliconflow.cn/i/2JCjsjdB`（邀请码 `2JCjsjdB`），新用户赠16元代金券（充值一分钱后可使用）。
2. 在控制台创建 API Key。
3. 配置到 `.env` 文件：
   ```bash
   LLM_API_KEY="sk-xxxxx"
   ```

### 费用说明

`Qwen3.5-35B-A3B` 输入仅 **¥0.0004 / K tokens**，每次验证码解算约 500 token，个人使用几乎免费。也可切换其他**多模态模型**：

| 模型                             | 输入价格          | 备注     |
| -------------------------------- | ----------------- | -------- |
| `Qwen/Qwen3.5-35B-A3B`         | ¥0.0004/K tokens | 推荐     |
| `Qwen/Qwen2.5-VL-72B-Instruct` | ¥0.004/K tokens  | 更准确   |
| `gpt-4o`                       | ~¥0.015/K tokens | OpenAI   |
| `doubao-seed-2-0-pro`          | ~¥0.004/K tokens | 火山引擎 |

> **注意：DeepSeek 全系列（V3/V4/R1）均为纯文本模型，不支持图片识别，无法用于验证码解算。**

### 我们使用的模型

本项目通过 OpenAI 兼容接口调用 **`Qwen/Qwen3.5-35B-A3B`** 多模态模型，能够准确识别：

- **图标点击验证码**：匹配参考图标到大图网格位置 → 返回点击坐标
- **文字顺序验证码**：读取提示文字顺序 → 在候选区定位汉字 → 按序点击
- **滑块验证码**：识别缺口位置 → 计算拖动距离

---

## 1）注册国家电网账户

首先要注册国家电网账户，绑定电表，并且可以手动查询电量

注册网址：[https://www.95598.cn/osgweb/login](https://www.95598.cn/osgweb/login)

## 2）获取HA token

  token获取方法参考[https://blog.csdn.net/qq_25886111/article/details/106282492](https://blog.csdn.net/qq_25886111/article/details/106282492)

## 3）docker镜像部署，速度快

1. 安装docker和homeassistant，[Homeassistant极简安装法](https://github.com/renhaiidea/easy-homeassistant)。
2. 克隆仓库

```bash
git clone https://github.com/ARC-MX/sgcc_electricity_new.git
# 如果github网络环境不好的话可以使用国内镜像，完全同步的，个人推荐使用国内镜像
# git clone https://gitee.com/ARC-MX/sgcc_electricity_new.git
cd sgcc_electricity_new
```

3. 创建环境变量文件

```bash
cp example.env .env
vim .env           # 参考以下文件编写.env文件
```

```bash
### 以下项都需要修改
## 国网登录信息
# 修改为自己的登录账号
PHONE_NUMBER="xxx" 
# 修改为自己的登录密码
PASSWORD="xxxx" 
# 排除指定用户ID，如果出现一些不想检测的ID或者有些充电、发电帐号、可以使用这个环境变量，如果有多个就用","分隔，","之间不要有空格
IGNORE_USER_ID=xxxxxxx,xxxxxxx,xxxxxxx

# 是否多户号模式，默认 false（单户号）
# 单户号：数据库表名不带户号后缀（daily / monthly / yearly / data），传感器实体名也不带户号后缀
# 多户号：表名带户号后缀（daily1234567890123），传感器实体名带户号后4位
MULTI_USER=false

# DB_TYPE 数据储存类型 SQLITE / MYSQL / POSTGRESQL(SUPABASE)，默认为NONE 不进行数据储存
DB_TYPE=NONE

# sqlite 数据库名，默认为homeassistant
DB_NAME="homeassistant.db"
# COLLECTION_NAME默认为electricity_daily_usage_{国网用户id}，不支持修改。

# mysql 的数据库配置
MYSQL_HOST="mysql.lan"
MYSQL_USER="user"
MYSQL_PASSWORD="password"
MYSQL_DATABASE="sgcc"
MYSQL_PORT=3306

# postgresql(supabase) 的数据库配置
# 方式一（推荐）：直接粘贴 Supabase 的 Connection string（URI）
#   项目页面 → Project Settings → Database → Connection string → URI
#   DB_TYPE=POSTGRESQL 时，下面三个变量名任选其一即可
# POSTGRES_URL="postgresql://postgres.xxxxx:你的密码@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
# SUPABASE_DB_URL="postgresql://postgres:你的密码@db.xxxxx.supabase.co:5432/postgres"
# DATABASE_URL="postgresql://postgres:你的密码@db.xxxxx.supabase.co:5432/postgres"
# 方式二：离散变量（未设置 POSTGRES_URL 时生效），默认 sslmode=require、端口 5432
# PG_HOST="db.xxxxx.supabase.co"
# PG_USER="postgres"
# PG_PASSWORD="password"
# PG_DATABASE="postgres"
# PG_PORT=5432
# PG_SSLMODE=require

## homeassistant配置
# 改为你的localhost为你的homeassistant地址
HASS_URL="http://localhost:8123/" 
# homeassistant的长期令牌
HASS_TOKEN="eyxxxxx"

## selenium运行参数
# 任务开始时间，24小时制，例如"07:00”则为每天早上7点执行，第一次启动程序如果时间晚于早上7点则会立即执行一次，每隔12小时执行一次。
JOB_START_TIME="07:00"
# 每次操作等待时间，推荐设定范围为[2,30]，该值表示每次点击网页后所要等待数据加载的时间，如果出现“no such element”诸如此类的错误可适当调大该值，如果硬件性能较好可以适当调小该值
RETRY_WAIT_TIME_OFFSET_UNIT=15


## 记录的天数, 仅支持填写 7 或 30
# 国网原本可以记录 30 天,现在不开通智能缴费只能查询 7 天造成错误
DATA_RETENTION_DAYS=7

## PUSH_TYPE 余额不足提醒方式 PUSHPLUS / URLPUSH ，默认为None不通知
PUSH_TYPE=None
# 余额提现阈值，通知类型PUSHPLUS / URLPUSH 都生效
BALANCE=5.0
# pushplus token 如果有多个就用","分隔，","之间不要有空格，单个就不要有","
PUSHPLUS_TOKEN=xxxxxxx,xxxxxxx,xxxxxxx

# url_push 地址 通知body {"user_id": user_id, "balance": balance}
PUSH_URL="http://push.lan/notify"

## MQTT 设备（可选，留空 MQTT_HOST 则使用 REST API，传感器不归入设备）
#MQTT_HOST="192.168.31.155"
#MQTT_PORT=1883
#MQTT_USERNAME=""
#MQTT_PASSWORD=""
#MQTT_DISCOVERY_PREFIX="homeassistant"

# 密码登录失败（登录次数过多等）的备选方案
LOGIN_FALLBACK='qrcode'
# 二维码的提交地址
PUSH_QRCODE_URL="http://push.lan/qrcode"

# 二维码扫描后的最多等待时间 = QR_CODE_LOGIN_WAIT_COUNT x QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT
# 测试发现二维码默认有效期限为60s
#二维码登录等待次数
QR_CODE_LOGIN_WAIT_COUNT=30
#二维码登录等待间隔
QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT=10

## 浏览器反检测参数（可选，默认值即可正常工作）
# 浏览器语言
# BROWSER_LANGUAGE=zh-HK,zh,en-US,en
# 浏览器窗口尺寸
# BROWSER_WINDOW_SIZE=1158,848
# 设备像素比
# BROWSER_DEVICE_SCALE_FACTOR=2
# 自定义 User-Agent（留空使用 Chrome 默认）
# BROWSER_USER_AGENT=

## ONNX Runtime 线程数限制（可选）
# 在 Docker 中限制了 CPU 数量时，设置此项可消除线程亲和度错误
# 建议设置为 Docker 分配的 CPU 核心数
# OP_NUM_THREADS=2

## 大模型验证码识别配置（必填）
# 默认硅基流动 Qwen 多模态模型（DeepSeek 等纯文本模型不支持图片识别）
LLM_API_KEY="your-api-key-here"
LLM_BASE_URL="https://api.siliconflow.cn/v1"
LLM_MODEL="Qwen/Qwen3.5-35B-A3B"

## 调试模式（可选）
# 设置为 true 启用调试模式（仅限本机运行，Docker 中无效）：
#   浏览器窗口可见 — 可观察完整自动化操作过程
#   短信验证码登录 — 通过弹窗输入短信码，验证码由 LLM 自动解算
# DEBUG_MODE=false

## 用户名映射（可选）
# 为每个户号指定友好名称，格式：户号:名称,户号:名称
# USER_NAMES="1234567890:家庭用电,0987654321:公司用电"
```

4. 运行

  我已经优化了镜像环境，将镜像的地址配置为阿里云，如果要使用docker hub的源可以将docker-compose.yml中
  image: registry.cn-hangzhou.aliyuncs.com/arcw/sgcc_electricity:latest 改为 arcw/sgcc_electricity:latest

```bash
运行获取传感器名称
docker-compose up -d
docker-compose logs sgcc_electricity_app
```

运行成功应该显示如下日志：

```bash
2026-07-24 07:00:15  [INFO    ] ---- 程序开始，当前仓库版本为1.x.x
2026-07-24 07:00:15  [INFO    ] ---- 当前登录的用户名为: xxxxxx
2026-07-24 07:00:16  [INFO    ] ---- Browser ready (standard + stealth)
2026-07-24 07:00:30  [INFO    ] ---- 已输入用户名: 138xxxxxxxx
2026-07-24 07:00:45  [INFO    ] ---- Cookie 有效，跳过登录
2026-07-24 07:00:52  [INFO    ] ---- [640xxxxxxxxx] 电费余额: 27.66 元
2026-07-24 07:01:05  [INFO    ] ---- [640xxxxxxxxx] 年度用电量: 1691 度, 年度电费: 758.57 元 (Vue)
2026-07-24 07:01:15  [INFO    ] ---- [640xxxxxxxxx] 2026-07: 用电 169 度, 电费 75.81 元 (Vue)
2026-07-24 07:01:20  [INFO    ] ---- [640xxxxxxxxx] 最近用电: 2026-07-23 6.56 度 (Vue)
2026-07-24 07:01:30  [INFO    ] ---- Home Assistant 传感器更新完成!
2024-06-06 16:01:55  [INFO    ] ---- Get month power charge for xxxxxxx successfully, 01 月 usage is xxx KWh, charge is xxx CNY.
2024-06-06 16:01:55  [INFO    ] ---- Get month power charge for xxxxxxx successfully, 02 月 usage is xxx KWh, charge is xxx CNY.
2024-06-06 16:01:55  [INFO    ] ---- Get month power charge for xxxxxxx successfully, 2024-03-01-2024-03-31 usage is xxx KWh, charge is xxx CNY.
2024-06-06 16:01:55  [INFO    ] ---- Get month power charge for xxxxxxx successfully, 2024-04-01-2024-04-30 usage is xxx KWh, charge is xxx CNY.
2024-06-06 16:01:59  [INFO    ] ---- Get daily power consumption for xxxxxxx successfully, , 2024-06-05 usage is xxx kwh.
........
2024-12-25 13:43:25  [INFO    ] ---- Check the electricity bill balance. When the balance is less than 100.0 CNY, the notification will be sent = True
2024-12-25 13:43:25  [INFO    ] ---- Homeassistant sensor sensor.electricity_charge_balance_xxxx state updated: 102.3 CNY
2024-12-25 13:43:25  [INFO    ] ---- Homeassistant sensor sensor.last_electricity_usage_xxxx state updated: 6.56 kWh
2024-12-25 13:43:25  [INFO    ] ---- Homeassistant sensor sensor.yearly_electricity_usage_xxxx state updated: 1691 kWh
2024-12-25 13:43:25  [INFO    ] ---- Homeassistant sensor sensor.yearly_electricity_charge_xxxx state updated: 758.57 CNY
2024-12-25 13:43:25  [INFO    ] ---- Homeassistant sensor sensor.month_electricity_usage_xxxx state updated: 169 kWh
2024-12-25 13:43:25  [INFO    ] ---- Homeassistant sensor sensor.month_electricity_charge_xxxx state updated: 75.81 CNY
2024-12-25 13:43:25  [INFO    ] ---- User xxxxxxx state-refresh task run successfully!
```

**sensor.electricity_charge_balance_xxxx 为余额传感器**

5. 配置configuration.yaml文件, 将下面中的_xxxx 替换为自己log中的_xxxx后缀。
6. 由于是API方式传递传感器数据，所以要想重启ha实体ID可用，必须配置如下

> 提示：也可以用 `python scripts/ha_template.py -o ha_template.yaml` 自动生成下面的配置
> （多户号加 `--user-id 6400001234`，实体名会自动补上 `_1234` 后缀），生成内容覆盖全部传感器。

```yaml
template:
  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.electricity_charge_balance_xxxx
    sensor:
      - name: electricity_charge_balance_xxxx
        unique_id: electricity_charge_balance_xxxx
        state: "{{ states('sensor.electricity_charge_balance_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "CNY"
        device_class: monetary

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.last_electricity_usage_xxxx
    sensor:
      - name: last_electricity_usage_xxxx
        unique_id: last_electricity_usage_xxxx
        state: "{{ states('sensor.last_electricity_usage_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.month_electricity_usage_xxxx
    sensor:
      - name: month_electricity_usage_xxxx
        unique_id: month_electricity_usage_xxxx
        state: "{{ states('sensor.month_electricity_usage_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.last_month_usage_xxxx
    sensor:
      - name: last_month_usage_xxxx
        unique_id: last_month_usage_xxxx
        state: "{{ states('sensor.last_month_usage_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.last_month_charge_xxxx
    sensor:
      - name: last_month_charge_xxxx
        unique_id: last_month_charge_xxxx
        state: "{{ states('sensor.last_month_charge_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "CNY"
        device_class: monetary

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.yearly_electricity_usage_xxxx
    sensor:
      - name: yearly_electricity_usage_xxxx
        unique_id: yearly_electricity_usage_xxxx
        state: "{{ states('sensor.yearly_electricity_usage_xxxx') }}"
        state_class: total_increasing
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.yearly_electricity_charge_xxxx
    sensor:
      - name: yearly_electricity_charge_xxxx
        unique_id: yearly_electricity_charge_xxxx
        state: "{{ states('sensor.yearly_electricity_charge_xxxx') }}"
        state_class: total_increasing
        unit_of_measurement: "CNY"
        device_class: monetary

```

配置完成后重启HA, 刷新一下HA界面

<img src="assets/restart.jpg" alt="restart.jpg" style="zoom: 50%;" />

6. 更新容器及其代码（需要更新才需要）

```bash
docker-compose down # 删除容器
docker-compose pull # 更新镜像
git pull --tags origin master:master	#更新代码，代码不在容器中，所以要手动更新
docker-compose up -d # 重新运行
#如果git 拉取失败可以执行如下命令，重新拉取
git fetch --all
git reset --hard origin/master
git pull
```

## 4）ha内数据展示

<img src="assets/edit1.jpg" alt="edit1.jpg" style="zoom: 50%;" />

结合[mini-graph-card](https://github.com/kalkih/mini-graph-card) 和[mushroom](https://github.com/piitaya/lovelace-mushroom)实现美化效果：

<img src="assets/Ha-mini-card.jpg" alt="Ha-mini-card.jpg" style="zoom: 50%;" />

将下面中的_xxxx 替换为自己log中的_xxxx后缀。

```yaml
type: vertical-stack
cards:
  - type: custom:mini-graph-card
    entities:
      - entity: sensor.last_electricity_usage_xxxx
        name: 国网每日用电量
        aggregate_func: first
        show_state: true
        show_points: true
        icon: mdi:lightning-bolt-outline
      - entity: sensor.electricity_charge_balance_xxxx
        name: 电费余额
        aggregate_func: first
        show_state: true
        show_points: true
        color: "#e74c3c"
        icon: mdi:cash
        y_axis: secondary
    group_by: date
    hour24: true
    hours_to_show: 240
    lower_bound: 0
    upper_bound: 10
    lower_bound_secondary: 0
    upper_bound_secondary: 120
    show:
      icon: false
  - type: horizontal-stack
    cards:
      - graph: none
        type: sensor
        entity: sensor.last_month_charge_xxxx
        detail: 1
        name: 上月电费
        icon: ""
        unit: 元
      - graph: none
        type: sensor
        entity: sensor.month_electricity_usage_xxxx
        detail: 1
        name: 上月用电量
        unit: 度
        icon: mdi:lightning-bolt-outline
  - type: horizontal-stack
    cards:
      - animate: true
        entities:
          - entity: sensor.yearly_electricity_usage_xxxx
            name: 今年总用电量
            aggregate_func: first
            show_state: true
            show_points: true
        group_by: date
        hour24: true
        hours_to_show: 240
        type: custom:mini-graph-card
      - animate: true
        entities:
          - entity: sensor.yearly_electricity_charge_xxxx
            name: 今年总用电费用
            aggregate_func: first
            show_state: true
            show_points: true
        group_by: date
        hour24: true
        hours_to_show: 240
        type: custom:mini-graph-card
```

## 5）电量通知

  更新电费余额不足提醒，在.env里设置提醒余额。目前我是用[pushplus](https://www.pushplus.plus/)的方案，注册pushplus然后，获取token，通知给谁就让谁注册并将token填到.env中
  token获取方法参考[https://cloud.tencent.com/developer/article/2139538](https://cloud.tencent.com/developer/article/2139538)

# 其他

> 当前作者：[https://github.com/ARC-MX/sgcc_electricity_new](https://github.com/ARC-MX/sgcc_electricity_new)
>
> 原作者：[https://github.com/louisslee/sgcc_electricity](https://github.com/louisslee/sgcc_electricity)，原始[README_origin.md](归档/README_origin.md)。

## 我的自定义部分包括：

增加的部分：

- 增加近30天每日电量写入数据库（默认mongodb），其他数据库请自行配置。
  - 添加配置默认增加近 7 天每日电量写入数据, 可修改为 30 天, 因为国网目前[「要签约智能交费才能看到30天的数据，不然就只能看到7天的」](https://github.com/ARC-MX/sgcc_electricity_new/issues/11#issuecomment-2158973048)。【注意：开通智能缴费后电费可能从「后付费」变为「预付费」，也就是「欠费即停电」，习惯了每月定时按账单缴费的需要注意，谨防停电风险】
- 将间歇执行设置为定时执行: JOB_START_TIME，24小时制，例如"07:00”则为每天早上7点执行，第一次启动程序立即执行一次, 每12小时执行一次
- 给last_daily_usage增加present_date，用来确定更新的是哪一天的电量。一般查询的日期会晚一到两天。
- 对configuration.yaml中自定义实体部分修改。

## 重要修改通知

* 2024-06-13：SQLite替换MongoDB，原因是python自带SQLite3，不需要额外安装，也不再需要MongoDB镜像。
* 2024-07-03：新增每天定时执行两次，添加配置默认增加近 7 天每日电量写入数据, 可修改为 30 天。
* 2024-07-05：新增余额不足提醒功能。
* 2024-12-10：新增忽略指定用户ID的功能：针对一些用户拥有充电或者发电账户，可以使用 IGNORE_USER_ID 环境变量忽略特定的ID。
* 2025-01-05：新增Homeassistant Add-on部署方式。
* 2025-05-01：**重大更新**：验证码识别从 ONNX 升级为**大模型（LLM）视觉识别**。浏览器反检测升级为 **Playwright + CloakBrowser**。
* 2025-05-20：Playwright + Chrome 反检测标志位方案，全面支持 ARMv6/v7 平台。
* 2025-07-24：**架构升级**：数据获取优先 Vue 状态注入（一次性提取全部数据），DOM 解析兜底。新增 DEBUG_MODE 短信登录 + 文字顺序验证码 LLM 识别。分时电量传感器优先使用账单数据。移除 Selenium 遗留代码。
  2025-05-15：新增**分时电量传感器**（谷/平/峰/尖）、**预付费余额传感器**、**应交金额传感器**；支持 Vue 状态直接注入提取数据。

### TO-DO

- [X] 增加离线滑动验证码识别方案 → 已升级为 LLM 视觉方案
- [X] 添加默认推送服务，电费余额不足提醒
- [X] 添加Homeassistant Add-on安装方式，在此感谢[Ami8834671](https://github.com/Ami8834671), [DuanXDong](https://github.com/DuanXDong)等小伙伴的idea和贡献
- [X] 添加大模型（LLM）验证码识别方案（硅基流动 Qwen）
- [X] 添加 CloakBrowser 反检测浏览器支持
- [X] 添加 Cookie 持久化机制（data/sgcc_cookies.json）
- [X] 添加 DEBUG_MODE 短信登录 + 文字顺序验证码 LLM 识别
- [X] 数据获取优先 Vue 状态注入，DOM 解析兜底
- [X] 分时电量传感器优先账单数据
- [ ] 添加 Home Assistant integration
- [X] 添加分时电量传感器（谷/平/峰/尖）在此感谢[renxiaoyaoo](https://github.com/renxiaoyaoo)的实现思路
- [X] 添加预付费余额/应交金额传感器
- [ ] 添加置Homeassistant integration

## **技术交流群**

由于现在用户越来越多，稍有问题大家就在github上发issue，我有点回复不过来了，故创建一个付费加入的QQ群。该群只是方便大家讨论，不承诺技术协助，我想大多数用户参考历史issue和文档都能解决自己的问题

### 再次说明，希望大家通过认真看文档和浏览历史issue解决问题，毕竟收费群不是开源项目的本意。
