"""
采集器基类
定义了采集器通用接口，实现了日志验证与丰富的基础逻辑。
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Generator
from datetime import datetime
import re

class BaseCollector(ABC):
    """采集器基类"""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.is_running = False
        
    @abstractmethod
    def collect(self) -> Generator[Dict[str, Any], None, None]:
        """采集日志，yield 每条日志字典"""
        pass
    
    @abstractmethod
    def start(self):
        """启动采集器"""
        pass
    
    @abstractmethod
    def stop(self):
        """停止采集器"""
        pass
    
    def validate_log(self, log_data: Dict[str, Any]) -> bool:
        """
        验证日志数据完整性
        要求必须包含: timestamp, log_type, source, message
        """
        required = ['timestamp', 'log_type', 'source', 'message']
        if not all(k in log_data for k in required):
            return False
        # 时间戳格式校验（ISO格式），兼容 'Z' 结尾的 UTC 表示
        try:
            ts = log_data['timestamp']
            # 将 'Z' 替换为 '+00:00'，因为 fromisoformat 不支持 'Z'
            if ts.endswith('Z'):
                ts = ts[:-1] + '+00:00'
            datetime.fromisoformat(ts)
        except Exception:
            return False
        # log_type 必须在预设范围内（可在子类中重写）
        return True
    
    def enrich_log(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        丰富日志：添加采集器名称、采集时间、唯一ID
        """
        enriched = log_data.copy()
        enriched['collector'] = self.name
        enriched['collected_at'] = datetime.now().isoformat()
        # 可选：生成简单消息ID
        import hashlib
        raw = f"{enriched.get('timestamp')}{enriched.get('source')}{enriched.get('message')}"
        enriched['msg_id'] = hashlib.md5(raw.encode()).hexdigest()[:16]
        return enriched