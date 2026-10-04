"""数据库抽象层：统一 SQLite / MySQL / PostgreSQL(Supabase) 接口，
子类只负责连接、SQL 方言与建表。

表结构（三种数据库一致，suffix 为多户号模式下的 user_id，单户号模式为空）：
- daily{suffix}:   date(PK), usage, peak_usage, valley_usage   -- 每日用电量
- monthly{suffix}: month(PK), charge, usage, peak_usage, valley_usage -- 每月电费/电量
- yearly{suffix}:  year(PK), charge, usage, peak_usage, valley_usage  -- 每年电费/电量
- data{suffix}:    name(PK), value                            -- 扩展数据（用户信息/余额日志等）
"""

import logging
import os
from urllib.parse import quote

from const import user_suffix

# ── SQLite ──
import sqlite3

# ── MySQL ──（可选，仅 DB_TYPE=mysql 时导入）
try:
    import mysql.connector
    _HAS_MYSQL = True
except ImportError:
    _HAS_MYSQL = False

# ── PostgreSQL ──（可选，仅 DB_TYPE=postgresql/supabase 时导入）
# 优先 psycopg2，其次 psycopg3，两者 API（connect/cursor/commit）一致
try:
    import psycopg2 as _psycopg
    _PG_DRIVER = "psycopg2"
except ImportError:
    try:
        import psycopg as _psycopg
        _PG_DRIVER = "psycopg3"
    except ImportError:
        _psycopg = None
        _PG_DRIVER = None
_HAS_PG = _psycopg is not None

# ── 固定列定义（首列为主键）──
DAILY_COLS = ("date", "usage", "peak_usage", "valley_usage")
MONTHLY_COLS = ("month", "charge", "usage", "peak_usage", "valley_usage")
YEARLY_COLS = ("year", "charge", "usage", "peak_usage", "valley_usage")
EXPAND_COLS = ("name", "value")

# ── 列类型（三种数据库通用：SQLite 动态类型，MySQL/PG 均支持 REAL / VARCHAR）──
DAILY_TYPES = {"date": "DATE", "usage": "REAL", "peak_usage": "REAL", "valley_usage": "REAL"}
MONTHLY_TYPES = {"month": "VARCHAR(20)", "charge": "REAL", "usage": "REAL",
                 "peak_usage": "REAL", "valley_usage": "REAL"}
YEARLY_TYPES = {"year": "VARCHAR(10)", "charge": "REAL", "usage": "REAL",
                "peak_usage": "REAL", "valley_usage": "REAL"}
EXPAND_TYPES = {"name": "VARCHAR(100)", "value": "TEXT"}


class DB:
    """数据库基类：定义统一数据操作接口。子类只需实现连接/执行/关闭/建表/方言。"""

    # ── 子类需覆写的方言属性 ──
    db_type: str = "base"

    # ── 子类需实现的方法 ──

    def _connect(self):
        raise NotImplementedError

    def _execute(self, sql: str):
        """执行一条写 SQL 并 commit。"""
        raise NotImplementedError

    def _close(self):
        raise NotImplementedError

    def _cursor(self):
        """返回一个可用游标（用于查询）。"""
        raise NotImplementedError

    def _upsert_sql(self, table: str, cols: tuple, values_sql: str) -> str:
        """生成"存在即更新"的方言 SQL。cols[0] 为主键列。"""
        raise NotImplementedError

    def _past_date_sql(self, days: int) -> str:
        """生成"N 天前的日期"的方言表达式（用于清理旧数据）。"""
        raise NotImplementedError

    def _quote(self, name: str) -> str:
        """标识符（表名/列名）的方言引号，SQLite 默认不加。"""
        return name

    # ── 通用工具 ──

    @staticmethod
    def _num(value):
        """转 float，无法转换时返回 None（写入 NULL）。"""
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _lit(value) -> str:
        """Python 值 → SQL 字面量（三种数据库通用的最小转义）。"""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    def _upsert(self, table: str, cols: tuple, values: tuple):
        values_sql = "(" + ", ".join(self._lit(v) for v in values) + ")"
        self._execute(self._upsert_sql(table, cols, values_sql))

    def _query(self, sql: str) -> list:
        """执行查询，返回 [{'列名': 值}, ...]。"""
        cursor = self._cursor()
        try:
            cursor.execute(sql)
            columns = [d[0] for d in (cursor.description or [])]
            rows = cursor.fetchall() or []
            return [dict(zip(columns, row)) for row in rows]
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def _select(self, table: str, key_col: str = None, key=None,
                order_by: str = None, limit: int = 0) -> list:
        """按主键（可选）查询一张表，结果在 Python 侧排序/截取，避免方言差异。"""
        sql = f"SELECT * FROM {self._quote(table)}"
        if key_col is not None:
            sql += f" WHERE {self._quote(key_col)} = {self._lit(key)}"
        rows = self._query(sql)
        if order_by:
            rows = [r for r in rows if r.get(order_by)]
            rows.sort(key=lambda r: str(r.get(order_by)))
        if limit and limit > 0:
            rows = rows[-limit:]
        return rows

    # ── 建表（子类只需提供引号方言）──

    def _create_table(self, table: str, cols: tuple, types: dict):
        defs = ",\n    ".join(
            f"{self._quote(c)} {types[c]}"
            + (" PRIMARY KEY NOT NULL" if i == 0 else "")
            for i, c in enumerate(cols))
        self._execute(f"CREATE TABLE IF NOT EXISTS {self._quote(table)} (\n    {defs})")
        # 旧版本表补列：已存在同名列时数据库会报错，忽略即可
        for col in cols[1:]:
            try:
                self._execute(
                    f"ALTER TABLE {self._quote(table)} "
                    f"ADD COLUMN {self._quote(col)} {types[col]}")
            except Exception:
                pass

    def _create_tables(self, user_id: str) -> bool:
        for table, cols, types in (
            (self.table_name, DAILY_COLS, DAILY_TYPES),
            (self.table_monthly_name, MONTHLY_COLS, MONTHLY_TYPES),
            (self.table_yearly_name, YEARLY_COLS, YEARLY_TYPES),
            (self.table_expand_name, EXPAND_COLS, EXPAND_TYPES),
        ):
            self._create_table(table, cols, types)
            logging.info(f"[{self.db_type}] 表 {table} OK")
        return True

    # ── 统一接口 ──

    def connect_user_db(self, user_id: str) -> bool:
        try:
            self._connect()
            suffix = user_suffix(user_id)
            self.table_name = f"daily{suffix}"
            self.table_monthly_name = f"monthly{suffix}"
            self.table_yearly_name = f"yearly{suffix}"
            self.table_expand_name = f"data{suffix}"
            return self._create_tables(user_id)
        except Exception as e:
            logging.error(f"[{self.db_type}] 连接/建表失败: {e}")
            return False

    def close_connect(self):
        try:
            self._close()
        except Exception:
            pass

    # ── 数据写入方法（子类共用，无需覆写） ──

    def upsert_user(self, user_id: str, username: str, user_name: str):
        self._upsert(self.table_expand_name, EXPAND_COLS,
                     ("user_info", f"{user_id}|{username}|{user_name}"))

    def insert_balance_log(self, data: dict):
        self._upsert(self.table_expand_name, EXPAND_COLS, (
            f"balance_{data.get('date', 'latest')}",
            f"{data.get('balance', 0)}|{data.get('user_name', '')}|"
            f"{data.get('as_of', '')}|{data.get('amount_due', '')}"))

    def insert_daily_data(self, data: dict):
        """每日数据：日期、用电量、峰值用电、峰谷用电"""
        self._upsert(self.table_name, DAILY_COLS, (
            data.get('date'),
            self._num(data.get('total_usage', data.get('usage'))),
            self._num(data.get('peak_usage')),
            self._num(data.get('valley_usage'))))

    def insert_monthly_data(self, data: dict):
        """每月数据：月份、电费、用电量、峰值用电、峰谷用电

        峰值 / 峰谷由每日表统计回填（Vue 不提供），因此传入为 None 时保留库中已有值。
        """
        month = data.get('month') or data.get('date', '')
        peak, valley = self._num(data.get('peak_usage')), self._num(data.get('valley_usage'))
        if peak is None or valley is None:
            existing = self._select(self.table_monthly_name, "month", month)
            if existing:
                peak = peak if peak is not None else self._num(existing[0].get('peak_usage'))
                valley = valley if valley is not None else self._num(existing[0].get('valley_usage'))
        self._upsert(self.table_monthly_name, MONTHLY_COLS, (
            month,
            self._num(data.get('total_charge', data.get('charge'))),
            self._num(data.get('total_usage', data.get('usage'))),
            peak,
            valley))

    def insert_yearly_data(self, data: dict):
        """每年数据：年份、电费、用电量、峰值用电、峰谷用电（峰值/峰谷同理保留旧值）"""
        year = data.get('year')
        peak, valley = self._num(data.get('peak_usage')), self._num(data.get('valley_usage'))
        if peak is None or valley is None:
            existing = self._select(self.table_yearly_name, "year", year)
            if existing:
                peak = peak if peak is not None else self._num(existing[0].get('peak_usage'))
                valley = valley if valley is not None else self._num(existing[0].get('valley_usage'))
        self._upsert(self.table_yearly_name, YEARLY_COLS, (
            year,
            self._num(data.get('total_charge', data.get('charge'))),
            self._num(data.get('total_usage', data.get('usage'))),
            peak,
            valley))

    def insert_data(self, data: dict):
        """原始每日数据写入（兼容旧调用）"""
        self._upsert(self.table_name, DAILY_COLS, (
            data.get('date'),
            self._num(data.get('usage', data.get('total_usage'))),
            self._num(data.get('peak_usage')),
            self._num(data.get('valley_usage'))))

    def insert_expand_data(self, data: dict):
        """原始扩展数据写入（兼容旧调用）"""
        self._upsert(self.table_expand_name, EXPAND_COLS,
                     (data.get('name'), data.get('value')))

    # ── 数据读取（传感器统一从这里取数）──

    def get_daily_data(self, days: int = 30) -> list:
        """最近 N 天日数据（按日期升序）；days<=0 返回全部。"""
        return self._select(self.table_name, order_by="date", limit=days)

    def get_monthly_data(self, months: int = 12) -> list:
        """最近 N 个月数据（按月份升序）；months<=0 返回全部。"""
        return self._select(self.table_monthly_name, order_by="month", limit=months)

    def get_yearly_data(self, year: str = None) -> dict:
        """年度数据；year 为空时返回最新一年。"""
        rows = self._select(self.table_yearly_name, order_by="year")
        if not rows:
            return {}
        if year is not None:
            return next((r for r in rows if str(r.get("year")) == str(year)), {})
        return rows[-1]

    def get_expand(self, name: str):
        """读取扩展表（data 表）中的值。"""
        rows = self._query(
            f"SELECT {self._quote('value')} FROM {self._quote(self.table_expand_name)} "
            f"WHERE {self._quote('name')} = {self._lit(name)}")
        return rows[0].get("value") if rows else None

    def get_latest_balance(self) -> dict:
        """最近一次余额记录（balance|user_name|as_of|amount_due）。"""
        raw = self.get_expand("balance_latest")
        if not raw:
            return {}
        parts = str(raw).split("|")

        def part(index):
            if index < len(parts) and parts[index] not in ("", "None"):
                return parts[index]
            return None

        return {
            "balance": self._num(part(0)),
            "user_name": part(1) or "",
            "as_of": part(2) or "",
            "amount_due": self._num(part(3)),
        }

    # ── 峰值 / 峰谷汇总：Vue 无此数据，由每日表统计 ──

    def _update_tou(self, table: str, key_col: str, key, peak: float, valley: float):
        """把某月/某年的峰值、峰谷写回表；行不存在时插入（电量/电费留空）。"""
        q = self._quote
        exists = self._query(
            f"SELECT {q(key_col)} FROM {q(table)} "
            f"WHERE {q(key_col)} = {self._lit(key)}")
        if exists:
            self._execute(
                f"UPDATE {q(table)} SET {q('peak_usage')} = {self._lit(peak)}, "
                f"{q('valley_usage')} = {self._lit(valley)} "
                f"WHERE {q(key_col)} = {self._lit(key)}")
        else:
            self._execute(
                f"INSERT INTO {q(table)} ({q(key_col)}, {q('peak_usage')}, {q('valley_usage')}) "
                f"VALUES ({self._lit(key)}, {self._lit(peak)}, {self._lit(valley)})")

    def refresh_tou_aggregate(self):
        """按每日表汇总每月/每年的峰值、峰谷电量，回写月表与年表。

        注意：数据库刚开始入库时统计会偏小（缺少更早的日数据），随时间累积会逐步准确。
        """
        def add(bucket, key, peak, valley):
            item = bucket.setdefault(
                key, {"peak": 0.0, "valley": 0.0, "has_peak": False, "has_valley": False})
            if peak is not None:
                item["peak"] += peak
                item["has_peak"] = True
            if valley is not None:
                item["valley"] += valley
                item["has_valley"] = True

        monthly, yearly = {}, {}
        for row in self.get_daily_data(days=0):
            date = str(row.get("date") or "")
            if len(date) < 7:
                continue
            peak = self._num(row.get("peak_usage"))
            valley = self._num(row.get("valley_usage"))
            if peak is None and valley is None:
                continue
            add(monthly, date[:7], peak, valley)
            add(yearly, date[:4], peak, valley)

        for month, item in sorted(monthly.items()):
            self._update_tou(self.table_monthly_name, "month", month,
                             item["peak"] if item["has_peak"] else None,
                             item["valley"] if item["has_valley"] else None)
        for year, item in sorted(yearly.items()):
            self._update_tou(self.table_yearly_name, "year", year,
                             item["peak"] if item["has_peak"] else None,
                             item["valley"] if item["has_valley"] else None)
        logging.info(f"[{self.db_type}] 峰值/峰谷汇总完成: "
                     f"{len(monthly)} 个月, {len(yearly)} 年")

    def cleanup_old_data(self):
        try:
            days = int(os.getenv("DATA_RETENTION_DAYS", 365))
            self._execute(
                f"DELETE FROM {self.table_name} "
                f"WHERE date < {self._past_date_sql(days)}")
        except Exception as e:
            logging.debug(f"[{self.db_type}] 清理旧数据失败（可忽略）: {e}")


# ═══════════════════════════════════════════════════════════
# SQLite 实现
# ═══════════════════════════════════════════════════════════

class SqliteDB(DB):
    db_type = "sqlite"

    def _connect(self):
        db_name = os.getenv("DB_NAME", "homeassistant.db")
        if "PYTHON_IN_DOCKER" in os.environ:
            db_name = "/data/" + db_name
        self._conn = sqlite3.connect(db_name)
        logging.info(f"[sqlite] 已连接 {db_name}")

    def _execute(self, sql: str):
        self._conn.execute(sql)
        self._conn.commit()

    def _close(self):
        if getattr(self, "_conn", None):
            self._conn.close()
            self._conn = None
            logging.info("[sqlite] 已关闭")

    def _cursor(self):
        return self._conn.cursor()

    def _upsert_sql(self, table: str, cols: tuple, values_sql: str) -> str:
        return f"INSERT OR REPLACE INTO {self._quote(table)} VALUES{values_sql}"

    def _past_date_sql(self, days: int) -> str:
        return f"date('now', '-{days} days')"


# ═══════════════════════════════════════════════════════════
# MySQL 实现
# ═══════════════════════════════════════════════════════════

class MysqlDB(DB):
    db_type = "mysql"

    def _connect(self):
        if not _HAS_MYSQL:
            raise RuntimeError("mysql-connector-python 未安装")
        self._conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST"),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
            database=os.getenv("MYSQL_DATABASE"),
            port=int(os.getenv("MYSQL_PORT", 3306)),
        )
        if self._conn.is_connected():
            logging.info(f"[mysql] 已连接 {os.getenv('MYSQL_DATABASE')}")
        else:
            raise ConnectionError("MySQL 连接失败")

    def _execute(self, sql: str):
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql)
            self._conn.commit()
        finally:
            cursor.close()

    def _close(self):
        if getattr(self, "_conn", None) and self._conn.is_connected():
            self._conn.close()
            self._conn = None
            logging.info("[mysql] 已关闭")

    def _cursor(self):
        return self._conn.cursor()

    def _quote(self, name: str) -> str:
        return f"`{name}`"

    def _upsert_sql(self, table: str, cols: tuple, values_sql: str) -> str:
        return f"REPLACE INTO {self._quote(table)} VALUES{values_sql}"

    def _past_date_sql(self, days: int) -> str:
        return f"DATE_SUB(CURDATE(), INTERVAL {days} DAY)"


# ═══════════════════════════════════════════════════════════
# PostgreSQL 实现（兼容 Supabase）
# ═══════════════════════════════════════════════════════════

class PostgresDB(DB):
    """PostgreSQL 实现，适用于 Supabase / 自建 PostgreSQL。

    连接串（三选一，推荐直接粘贴 Supabase 的 URI）：
        POSTGRES_URL / SUPABASE_DB_URL / DATABASE_URL
        postgresql://postgres.<ref>:<password>@<host>:<port>/postgres
    或离散环境变量：
        PG_HOST / PG_USER / PG_PASSWORD / PG_DATABASE / PG_PORT / PG_SSLMODE
    未显式指定时自动追加 sslmode=require（Supabase 强制要求 SSL）。
    """

    db_type = "postgresql"
    _URL_KEYS = ("POSTGRES_URL", "SUPABASE_DB_URL", "DATABASE_URL", "PG_URL", "PG_DSN")

    # ── 连接 ──

    @staticmethod
    def _append_param(url: str, key: str, value: str) -> str:
        if f"{key}=" in url:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{key}={value}"

    def _dsn(self) -> str:
        for key in self._URL_KEYS:
            url = (os.getenv(key) or "").strip()
            if url:
                url = self._append_param(url, "sslmode", "require")
                url = self._append_param(url, "connect_timeout", "10")
                return url

        host = os.getenv("PG_HOST")
        if not host:
            raise RuntimeError(
                "PostgreSQL 未配置：请设置 POSTGRES_URL（Supabase 连接串）"
                "或 PG_HOST/PG_USER/PG_PASSWORD")
        user = quote(os.getenv("PG_USER", "postgres"), safe="")
        password = quote(os.getenv("PG_PASSWORD", ""), safe="")
        database = os.getenv("PG_DATABASE", "postgres")
        port = os.getenv("PG_PORT", "5432")
        sslmode = os.getenv("PG_SSLMODE", "require")
        return (f"postgresql://{user}:{password}@{host}:{port}/{database}"
                f"?sslmode={sslmode}&connect_timeout=10")

    def _connect(self):
        if not _HAS_PG:
            raise RuntimeError("未安装 PostgreSQL 驱动：pip install psycopg2-binary")
        dsn = self._dsn()
        self._dsn_str = dsn
        self._conn = _psycopg.connect(dsn)
        logging.info(f"[postgresql] 已连接 {self._masked_dsn()} (driver={_PG_DRIVER})")

    def _masked_dsn(self) -> str:
        dsn = getattr(self, "_dsn_str", "")
        if "://" not in dsn:
            return dsn
        head, _, rest = dsn.partition("://")
        cred, _, tail = rest.rpartition("@")
        if not tail:                      # 无凭据
            return dsn
        user = cred.split(":", 1)[0]
        return f"{head}://{user}:***@{tail}"

    def _conn_alive(self) -> bool:
        conn = getattr(self, "_conn", None)
        return conn is not None and not getattr(conn, "closed", 0)

    def _ensure_conn(self):
        if not self._conn_alive():
            logging.warning("[postgresql] 连接不可用，尝试重连")
            self._connect()
        return self._conn

    # ── 执行 ──

    def _run(self, sql: str):
        conn = self._ensure_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def _execute(self, sql: str):
        try:
            self._run(sql)
        except Exception as e:
            # 连接断开时重连后重试一次
            if self._conn_alive():
                raise
            logging.warning(f"[postgresql] 执行失败({e})，重连后重试")
            try:
                self._close()
            except Exception:
                pass
            self._run(sql)

    def _close(self):
        if self._conn_alive():
            self._conn.close()
        self._conn = None
        logging.info("[postgresql] 已关闭")

    # ── 建表 ──

    # ── 方言 ──

    def _cursor(self):
        return self._ensure_conn().cursor()

    def _quote(self, name: str) -> str:
        return f'"{name}"'

    def _upsert_sql(self, table: str, cols: tuple, values_sql: str) -> str:
        q = self._quote
        cols_sql = ", ".join(q(c) for c in cols)
        pk = cols[0]
        updates = [f'{q(c)} = EXCLUDED.{q(c)}' for c in cols[1:]]
        action = "UPDATE SET " + ", ".join(updates) if updates else "NOTHING"
        return (f'INSERT INTO {q(table)} ({cols_sql}) VALUES{values_sql} '
                f'ON CONFLICT ({q(pk)}) DO {action}')

    def _past_date_sql(self, days: int) -> str:
        return f"(CURRENT_DATE - INTERVAL '{days} days')"


# ═══════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════

_PG_ALIASES = ("postgresql", "postgres", "postgre", "pg", "supabase")


def create_db(db_type: str) -> DB:
    """根据配置创建数据库实例。"""
    t = (db_type or "").lower()
    if t == "mysql":
        return MysqlDB()
    if t in _PG_ALIASES:
        return PostgresDB()
    return SqliteDB()
