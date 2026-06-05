# AI 分析返回优化基线指摘

## 背景与目标

### 背景
在用户实体行为分析（UEBA）系统中，用户基线是判断用户行为是否异常的重要依据。传统的基线构建主要基于统计分析（5W1H：Who, What, When, Where, Why, How），但这种方式可能存在以下问题：
- 基线规则过于静态，难以适应动态变化的行为模式
- 人工定义的阈值可能不够准确
- 缺乏对复杂行为模式的深度理解

### 目标
通过引入 AI 大模型能力，对用户基线进行智能优化，生成更精准、更动态的行为基线指摘，提升异常检测的准确性和适应性。

## 技术方案

### 核心思路
利用大模型的强大理解能力，分析用户历史行为数据，识别潜在的行为模式，生成优化的基线建议。

### 关键技术点
1. **用户基线分析算法**：基于 5W1H 或更多维度构建基础基线
2. **数据持久化**：将用户基线内容存储到数据库（ClickHouse）
3. **AI 智能分析**：通过 `ai_client` 调用大模型 API
4. **上下文优化**：减少不必要的数据传输，降低 API 调用成本

## 详细流程

### 1. 基础基线构建
```python
# 从用户行为数据中提取 5W1H 特征
def extract_baseline_features(user_logs):
    return {
        'who': user_logs['username'],           # 用户身份
        'what': user_logs['action'],           # 行为类型
        'when': extract_time_patterns(user_logs),  # 时间模式
        'where': extract_location_patterns(user_logs),  # 地理位置
        'why': infer_intent(user_logs),        # 行为意图
        'how': extract_method_patterns(user_logs)  # 操作方式
    }
```

### 2. 数据存储
```python
# 将基线数据存储到 ClickHouse
def store_baseline_to_db(user_baseline):
    baseline_store = BaselineStore(clickhouse_client)
    baseline_store.save_baseline(user_baseline)
```

### 3. AI 优化分析
```python
# 调用大模型进行基线优化
async def optimize_baseline_with_ai(user_baseline):
    ai_client = AIClient(
        api_key=settings.zhipu_api_key,
        platform="zhipu",
        model="glm-4-flash"
    )
    
    # 构建优化的 prompt
    prompt = f"""
    基于以下用户行为基线，分析并优化异常检测规则：
    
    用户基线数据：
    {format_baseline_for_ai(user_baseline)}
    
    请提供：
    1. 当前基线的潜在问题
    2. 优化建议和新的阈值
    3. 需要关注的异常模式
    4. 行为基线强化策略
    """
    
    response = await ai_client.chat(prompt)
    return parse_ai_response(response)
```

### 4. 优化流程（减少上下文使用）

#### 阶段一：行为基线筛选
```python
def filter_baseline_data(raw_baseline, importance_threshold=0.7):
    """筛选重要的基线特征，减少数据传输量"""
    filtered_features = {}
    
    for feature_name, feature_value in raw_baseline.items():
        importance = calculate_feature_importance(feature_name, feature_value)
        if importance >= importance_threshold:
            filtered_features[feature_name] = feature_value
    
    return filtered_features
```

#### 阶段二：大模型采样
```python
def sample_for_ai_analysis(filtered_baseline, sample_ratio=0.3):
    """对筛选后的数据进行智能采样"""
    # 基于特征重要性和多样性进行采样
    sampled_data = smart_sampling(
        filtered_baseline, 
        sample_ratio=sample_ratio,
        strategy='diverse_importance'
    )
    return sampled_data
```

#### 阶段三：强化行为基线
```python
def reinforce_baseline(original_baseline, ai_suggestions):
    """根据 AI 建议强化行为基线"""
    reinforced_baseline = original_baseline.copy()
    
    # 应用 AI 建议的优化
    for suggestion in ai_suggestions:
        if suggestion['type'] == 'threshold_adjustment':
            reinforced_baseline[suggestion['feature']] = suggestion['new_value']
        elif suggestion['type'] == 'new_pattern':
            reinforced_baseline['patterns'].append(suggestion['pattern'])
    
    return reinforced_baseline
```

## 完整实现示例

### 主流程编排
```python
class BaselineOptimizer:
    """基线优化器，整合基线构建、存储和 AI 优化"""
    
    def __init__(self, clickhouse_client, ai_client):
        self.clickhouse_client = clickhouse_client
        self.ai_client = ai_client
        self.baseline_store = BaselineStore(clickhouse_client)
        
    async def optimize_user_baseline(self, username, time_window_days=30):
        """完整的基线优化流程"""
        
        # 1. 构建基础基线
        raw_logs = self._fetch_user_logs(username, time_window_days)
        baseline_features = self._extract_5w1h_features(raw_logs)
        
        # 2. 存储基础基线
        baseline_id = self.baseline_store.save_baseline({
            'username': username,
            'features': baseline_features,
            'created_at': datetime.now(),
            'version': '1.0'
        })
        
        # 3. 筛选重要特征
        filtered_features = filter_baseline_data(baseline_features)
        
        # 4. 采样数据用于 AI 分析
        sampled_data = sample_for_ai_analysis(filtered_features)
        
        # 5. 调用 AI 进行优化分析
        ai_suggestions = await self._get_ai_optimization_suggestions(
            username, sampled_data
        )
        
        # 6. 强化基线
        reinforced_baseline = reinforce_baseline(
            baseline_features, ai_suggestions
        )
        
        # 7. 存储优化后的基线
        optimized_baseline_id = self.baseline_store.save_baseline({
            'username': username,
            'features': reinforced_baseline,
            'created_at': datetime.now(),
            'version': '2.0',
            'parent_baseline_id': baseline_id,
            'ai_suggestions': ai_suggestions
        })
        
        return {
            'original_baseline_id': baseline_id,
            'optimized_baseline_id': optimized_baseline_id,
            'ai_suggestions': ai_suggestions
        }
    
    async def _get_ai_optimization_suggestions(self, username, sampled_data):
        """获取 AI 优化建议"""
        prompt = self._build_optimization_prompt(username, sampled_data)
        
        response = await self.ai_client.chat(
            prompt,
            temperature=0.3,  # 降低随机性，获得更稳定的建议
            max_tokens=2000
        )
        
        return self._parse_ai_suggestions(response)
```

## 优化策略

### 1. 数据传输优化
- **特征筛选**：只传输重要性高的特征
- **智能采样**：基于多样性和重要性进行采样
- **数据压缩**：使用紧凑的 JSON 格式

### 2. 成本控制
- **批量处理**：将多个用户的基线合并分析
- **缓存机制**：缓存相似用户的分析结果
- **增量更新**：只分析变化的部分

### 3. 质量保证
- **多轮验证**：对 AI 建议进行验证和筛选
- **人工审核**：关键建议需要人工确认
- **A/B 测试**：对比优化前后的检测效果

## 配置参数

```python
# 基线优化配置
BASELINE_OPTIMIZATION_CONFIG = {
    # 数据筛选参数
    'feature_importance_threshold': 0.7,
    'sample_ratio': 0.3,
    
    # AI 调用参数
    'ai_temperature': 0.3,
    'ai_max_tokens': 2000,
    'ai_model': 'glm-4-flash',
    
    # 优化策略
    'enable_batch_processing': True,
    'batch_size': 10,
    'enable_cache': True,
    'cache_ttl_hours': 24,
    
    # 质量控制
    'enable_human_review': True,
    'enable_ab_testing': True,
    'min_confidence_score': 0.8
}
```

## 监控指标

### 效果指标
- **检测准确率**：优化后异常检测的准确率提升
- **误报率**：优化后误报率的降低
- **基线覆盖率**：基线对用户行为的覆盖程度

### 性能指标
- **API 调用次数**：AI API 的调用频率
- **响应时间**：基线优化的平均响应时间
- **成本消耗**：AI API 的成本消耗

### 质量指标
- **建议采纳率**：AI 建议被采纳的比例
- **基线稳定性**：基线随时间的稳定性
- **用户满意度**：安全人员对优化结果的满意度

## 后续优化方向

1. **自适应学习**：根据检测结果自动调整基线
2. **联邦学习**：在保护隐私的前提下共享基线知识
3. **实时优化**：支持实时的基线更新和优化
4. **多模态分析**：结合多种数据源进行综合分析

---

**文档版本**: v1.0  
**最后更新**: 2026-06-05  
**维护者**: AI 助手团队