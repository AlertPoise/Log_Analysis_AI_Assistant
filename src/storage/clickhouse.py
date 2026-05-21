"""
ClickHouse 客户端 - 支持可移植配置
"""

import logging
import time
from typing import Any, Dict, List, Optional

import clickhouse_connect
from clickhouse_connect.driver import Client
from clickhouse_connect.driver.exceptions import ClickHouseError

from ..utils.logger import get_logger
from ..utils.config import settings  # 导入全局配置

logger = get_logger(__name__)


class ClickHouseClient:
    """
    ClickHouse 客户端，支持从配置文件、环境变量或全局 settings 初始化。
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        timeout: int = 30,
        compress: bool = False,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化客户端参数。
        优先级：直接参数 > config 字典 > 环境变量/全局配置 > 硬编码默认值。

        Args:
            host: ClickHouse 服务器地址
            port: 端口 (默认 8123)
            username: 用户名
            password: 密码
            database: 数据库名
            timeout: 连接超时（秒）
            compress: 是否压缩
            config: 旧版配置字典（兼容性保留）
        """
        # 从 config 字典中提取（如果提供）
        if config:
            host = host or config.get('host')
            port = port or config.get('port')
            username = username or config.get('username')
            password = password or config.get('password')
            database = database or config.get('database')
            timeout = config.get('timeout', timeout)
            compress = config.get('compress', compress)

        # 如果仍未设置，尝试从全局 settings 读取
        if host is None:
            host = getattr(settings, 'clickhouse_host', 'localhost')
        if port is None:
            port = getattr(settings, 'clickhouse_port', 8123)
        if username is None:
            username = getattr(settings, 'clickhouse_user', 'default')
        if password is None:
            password = getattr(settings, 'clickhouse_password', '')
        if database is None:
            database = getattr(settings, 'clickhouse_database', 'default')

        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._database = database
        self._timeout = timeout
        self._compress = compress

        self.client: Optional[Client] = None
        self._connected = False

    @classmethod
    def from_settings(cls, **overrides) -> "ClickHouseClient":
        """
        从全局 settings 创建客户端实例，允许覆盖部分参数。

        Args:
            **overrides: 可覆盖 host, port, username, password, database, timeout, compress
        Returns:
            ClickHouseClient 实例
        """
        return cls(
            host=overrides.get('host'),
            port=overrides.get('port'),
            username=overrides.get('username'),
            password=overrides.get('password'),
            database=overrides.get('database'),
            timeout=overrides.get('timeout', 30),
            compress=overrides.get('compress', False),
        )

    def connect(self, retries: int = 3, retry_delay: float = 1.0) -> None:
        """
        建立与 ClickHouse 的连接，支持自动重试。

        Args:
            retries: 最大重试次数
            retry_delay: 重试间隔（秒）
        """
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                self.client = clickhouse_connect.get_client(
                    host=self._host,
                    port=self._port,
                    username=self._username,
                    password=self._password,
                    database=self._database,
                    send_receive_timeout=self._timeout,
                    compress=self._compress,
                )
                # 测试连接
                self.client.command('SELECT 1')
                self._connected = True
                logger.info(f"成功连接到 ClickHouse: {self._host}:{self._port}")
                return
            except ClickHouseError as e:
                last_error = e
                logger.warning(f"连接失败 (尝试 {attempt}/{retries}): {e}")
                if attempt < retries:
                    time.sleep(retry_delay)
        logger.error(f"连接 ClickHouse 最终失败: {last_error}")
        raise last_error

    def insert_logs(self, table: str, logs: List[Dict[str, Any]]) -> int:
        """
        批量插入日志数据（与原接口完全一致）
        """
        if not self._connected or self.client is None:
            raise RuntimeError("ClickHouse 未连接，请先调用 connect()")

        if not logs:
            logger.debug("insert_logs 收到空列表，跳过插入")
            return 0

        try:
            columns = list(logs[0].keys())
            rows = [[log.get(col) for col in columns] for log in logs]
            self.client.insert(table=table, data=rows, column_names=columns)
            logger.debug(f"成功插入 {len(logs)} 条日志到表 {table}")
            return len(logs)
        except ClickHouseError as e:
            logger.error(f"批量插入日志失败: {e}")
            raise

    def query_logs(
        self,
        table: str,
        conditions: Optional[Dict[str, Any]] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        条件查询（与原接口完全一致）
        """
        if not self._connected or self.client is None:
            raise RuntimeError("ClickHouse 未连接，请先调用 connect()")

        where_clause = ""
        params = {}
        if conditions:
            clauses = []
            for field, value in conditions.items():
                clauses.append(f"{field} = %({field})s")
                params[field] = value
            where_clause = "WHERE " + " AND ".join(clauses)

        query = f"SELECT * FROM {table} {where_clause} LIMIT {limit}"
        try:
            result = self.client.query(query, parameters=params)
            rows = [dict(zip(result.column_names, row)) for row in result.result_rows]
            logger.debug(f"查询返回 {len(rows)} 条记录")
            return rows
        except ClickHouseError as e:
            logger.error(f"查询日志失败: {e}")
            raise

    def aggregate(
        self,
        table: str,
        metrics: List[str],
        group_by: List[str],
        conditions: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        聚合查询（与原接口完全一致）
        """
        if not self._connected or self.client is None:
            raise RuntimeError("ClickHouse 未连接，请先调用 connect()")

        select_fields = group_by + metrics
        select_str = ", ".join(select_fields)
        where_clause = ""
        params = {}
        if conditions:
            clauses = []
            for field, value in conditions.items():
                clauses.append(f"{field} = %({field})s")
                params[field] = value
            where_clause = "WHERE " + " AND ".join(clauses)

        group_str = ", ".join(group_by)
        query = f"SELECT {select_str} FROM {table} {where_clause} GROUP BY {group_str}"
        try:
            result = self.client.query(query, parameters=params)
            rows = [dict(zip(result.column_names, row)) for row in result.result_rows]
            logger.debug(f"聚合查询返回 {len(rows)} 条记录")
            return rows
        except ClickHouseError as e:
            logger.error(f"聚合查询失败: {e}")
            raise

    def close(self) -> None:
        """关闭连接"""
        if self.client:
            self.client.close()
            self._connected = False
            logger.info("ClickHouse 连接已关闭")