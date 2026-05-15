"""
Elasticsearch 客户端 (启动过慢暂未测试)

日志索引、全文搜索和聚合分析功能。
"""

from typing import Any, Dict, List, Optional

from elasticsearch import Elasticsearch, exceptions as es_exceptions
from elasticsearch.helpers import bulk

from ..utils.logger import get_logger

logger = get_logger(__name__)


class ElasticsearchClient:
    """Elasticsearch 客户端，负责日志索引与检索"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化客户端
        Args:
            config: 配置字典，包含：
                - hosts: ES 地址列表，如 ['localhost:9200']
                - (可选)http_auth (username, password), use_ssl, verify_certs, index_prefix
        """
        self.config = config
        self.client: Optional[Elasticsearch] = None
        self._connected = False

    def connect(self) -> None:
        """建立与 Elasticsearch 的连接"""
        try:
            hosts = self.config.get('hosts', ['localhost:9200'])
            kwargs = {}
            if 'http_auth' in self.config:
                kwargs['http_auth'] = (self.config['http_auth']['username'],
                                       self.config['http_auth']['password'])
            if self.config.get('use_ssl', False):
                kwargs['use_ssl'] = True
                kwargs['verify_certs'] = self.config.get('verify_certs', False)

            self.client = Elasticsearch(hosts, **kwargs)
            # 测试连接
            if self.client.ping():
                self._connected = True
                logger.info(f"成功连接到 Elasticsearch: {hosts}")
            else:
                raise es_exceptions.ConnectionError("Ping 失败")
        except es_exceptions.ElasticsearchException as e:
            logger.error(f"连接 Elasticsearch 失败: {e}")
            raise
        except Exception as e:
            logger.error(f"连接时发生未知错误: {e}")
            raise

    def index_log(self, index: str, log_data: Dict[str, Any], doc_id: Optional[str] = None) -> bool:
        """
        索引单条日志
        Args:
            index: 索引名称
            log_data: 日志数据字典
            doc_id: 文档 ID
        Returns:
            是否成功
        """
        if not self._connected or self.client is None:
            raise RuntimeError("Elasticsearch 未连接，请先调用 connect()")

        try:
            response = self.client.index(index=index, body=log_data, id=doc_id)
            if response['result'] in ('created', 'updated'):
                logger.debug(f"文档索引成功: {response['_id']}")
                return True
            else:
                logger.warning(f"文档索引结果异常: {response}")
                return False
        except es_exceptions.ElasticsearchException as e:
            logger.error(f"索引日志失败: {e}")
            return False

    def index_bulk(self, index: str, logs: List[Dict[str, Any]]) -> int:
        """
        批量索引日志
        Args:
            index: 索引名称
            logs: 日志数据列表
        Returns:
            成功索引的文档数量
        """
        if not self._connected or self.client is None:
            raise RuntimeError("Elasticsearch 未连接，请先调用 connect()")

        actions = [
            {
                "_index": index,
                "_source": log
            }
            for log in logs
        ]

        try:
            success, failed = bulk(self.client, actions, stats_only=True, raise_on_error=False)
            logger.info(f"批量索引完成: 成功 {success} 条, 失败 {len(failed) if failed else 0} 条")
            return success
        except es_exceptions.ElasticsearchException as e:
            logger.error(f"批量索引失败: {e}")
            return 0

    def search(self, index: str, query: Dict[str, Any], size: int = 100) -> List[Dict[str, Any]]:
        """
        搜索日志
        Args:
            index: 索引名称
            query: Elasticsearch DSL 查询体
            size: 返回文档数量
        Returns:
            搜索结果列表，每个元素为 _source 内容
        """
        if not self._connected or self.client is None:
            raise RuntimeError("Elasticsearch 未连接，请先调用 connect()")

        try:
            response = self.client.search(index=index, body=query, size=size)
            hits = response['hits']['hits']
            results = [hit['_source'] for hit in hits]
            logger.debug(f"搜索返回 {len(results)} 条结果")
            return results
        except es_exceptions.ElasticsearchException as e:
            logger.error(f"搜索失败: {e}")
            return []

    def aggregate(self, index: str, aggs: Dict[str, Any], query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        聚合分析
        Args:
            index: 索引名称
            aggs: 聚合定义
            query: 可选的查询过滤条件
        Returns:
            聚合结果字典
        """
        if not self._connected or self.client is None:
            raise RuntimeError("Elasticsearch 未连接，请先调用 connect()")

        body = {"aggs": aggs}
        if query:
            body["query"] = query

        try:
            response = self.client.search(index=index, body=body, size=0)
            return response.get('aggregations', {})
        except es_exceptions.ElasticsearchException as e:
            logger.error(f"聚合查询失败: {e}")
            return {}

    def close(self) -> None:
        """关闭连接"""
        if self.client:
            self.client.close()
            self._connected = False
            logger.info("Elasticsearch 连接已关闭")