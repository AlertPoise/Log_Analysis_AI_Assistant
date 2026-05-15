"""
ClickHouse 客户端
"""

import logging
from typing import Any, Dict, List, Optional

import clickhouse_connect
from clickhouse_connect.driver import Client
from clickhouse_connect.driver.exceptions import ClickHouseError

from ..utils.logger import get_logger

logger = get_logger(__name__)


class ClickHouseClient:

    def __init__(self, config: Dict[str, Any]):
        """
        初始化客户端
        Args:
            config:
                - host: ClickHouse 服务器地址
                - port: 端口 (默认 8123)
                - username: 用户名
                - password: 密码
                - database: 数据库名
                - (可选)timeout, compress
        """
        self.config = config
        self.client: Optional[Client] = None
        self._connected = False

    def connect(self) -> None:
        """建立与 ClickHouse 的连接"""
        try:
            self.client = clickhouse_connect.get_client(
                host=self.config.get('host', 'localhost'),
                port=self.config.get('port', 8123),
                username=self.config.get('username', 'default'),
                password=self.config.get('password', ''),
                database=self.config.get('database', 'default'),
                # 可选参数
                send_receive_timeout=self.config.get('timeout', 30),
                compress=self.config.get('compress', False),
            )
            # 测试连接
            self.client.command('SELECT 1')
            self._connected = True
            logger.info(f"成功连接到 ClickHouse: {self.config.get('host')}:{self.config.get('port')}")
        except ClickHouseError as e:
            logger.error(f"连接 ClickHouse 失败: {e}")
            raise

    def insert_logs(self, table: str, logs: List[Dict[str, Any]]) -> int:
        """
        批量插入日志数据
        Args:
            table: 目标表名
            logs: 日志字典列表，每个字典需包含以下字段：
                - timestamp (datetime 或 str)
                - log_type (str)
                - source (str)
                - message (str)
                - host (str, optional)
                - offset (int, optional)
                - partition (int, optional)
                - collector (str)
                - collected_at (datetime 或 str)
                - msg_id (str)
        Returns:
            实际插入的行数
        Raises:
            RuntimeError: 未连接时调用
            ClickHouseError: 插入失败
        """
        if not self._connected or self.client is None:
            raise RuntimeError("ClickHouse 未连接，请先调用 connect()")

        if not logs:
            logger.debug("insert_logs 收到空列表，跳过插入")
            return 0

        try:
            # 准备列名和数据行
            # 提取第一条日志的键作为列名
            columns = list(logs[0].keys())
            rows = [[log.get(col) for col in columns] for log in logs]

            # 执行批量插入
            self.client.insert(table=table, data=rows, column_names=columns)

            logger.debug(f"成功插入 {len(logs)} 条日志到表 {table}")
            return len(logs)

        except ClickHouseError as e:
            logger.error(f"批量插入日志失败: {e}")
            raise

    def query_logs(self, table: str, conditions: Optional[Dict[str, Any]] = None,
                   limit: int = 1000) -> List[Dict[str, Any]]:
        """
        条件查询
        Args:
            table: 表名
            conditions: 字段条件字典
            limit: 返回记录数上限
        Returns:
            查询结果列表，每行为字典
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
            # result.result_rows 为行列表，result.column_names 为列名列表
            rows = []
            for row in result.result_rows:
                rows.append(dict(zip(result.column_names, row)))
            logger.debug(f"查询返回 {len(rows)} 条记录")
            return rows
        except ClickHouseError as e:
            logger.error(f"查询日志失败: {e}")
            raise

    def aggregate(self, table: str, metrics: List[str], group_by: List[str],
                  conditions: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        聚合查询
        Args:
            table: 表名
            metrics: 聚合指标，如 ['count()', 'avg(response_time)']
            group_by: 分组字段列表
            conditions: 可选的 WHERE 条件
        Returns:
            聚合结果列表
        """
        if not self._connected or self.client is None:
            raise RuntimeError("ClickHouse 未连接，请先调用 connect()")

        # 构建 SELECT 子句
        select_fields = group_by + metrics
        select_str = ", ".join(select_fields)

        # 构建 WHERE 子句
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