"""UEBA Service 编排模块测试。"""

from datetime import datetime

from src.behavior.config import UebaBaselineConfig
from src.behavior.schemas import BaselineBuildResult, UserAggregateFeature, UserBaseline
from src.behavior.service import UebaService


class FakeRepository:
    """记录 Service 调用的 fake Repository。"""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def _record(self, name, log_type, result):
        if self.fail:
            raise RuntimeError("repository failed")
        self.calls.append((name, log_type))
        return result

    def fetch_user_summary(self, start_time, end_time, log_type="vpn"):
        return self._record("fetch_user_summary", log_type, [{"username": "zhangsan", "sample_count": 20}])

    def fetch_hour_distribution(self, start_time, end_time, log_type="vpn"):
        return self._record("fetch_hour_distribution", log_type, [])

    def fetch_top_source_ips(self, start_time, end_time, limit, log_type="vpn"):
        self.calls.append(("fetch_top_source_ips", limit, log_type))
        return []

    def fetch_top_destination_ips(self, start_time, end_time, limit, log_type="vpn"):
        self.calls.append(("fetch_top_destination_ips", limit, log_type))
        return []

    def fetch_top_source_countries(self, start_time, end_time, limit, log_type="vpn"):
        self.calls.append(("fetch_top_source_countries", limit, log_type))
        return []

    def fetch_top_source_cities(self, start_time, end_time, limit, log_type="vpn"):
        self.calls.append(("fetch_top_source_cities", limit, log_type))
        return []

    def fetch_top_vpn_gateways(self, start_time, end_time, limit, log_type="vpn"):
        self.calls.append(("fetch_top_vpn_gateways", limit, log_type))
        return []

    def fetch_action_distribution(self, start_time, end_time, log_type="vpn"):
        return self._record("fetch_action_distribution", log_type, [])

    def fetch_event_type_distribution(self, start_time, end_time, log_type="vpn"):
        return self._record("fetch_event_type_distribution", log_type, [])

    def fetch_result_distribution(self, start_time, end_time, log_type="vpn"):
        return self._record("fetch_result_distribution", log_type, [])

    def fetch_fail_reason_distribution(self, start_time, end_time, limit, log_type="vpn"):
        self.calls.append(("fetch_fail_reason_distribution", limit, log_type))
        return []

    def fetch_auth_method_distribution(self, start_time, end_time, log_type="vpn"):
        return self._record("fetch_auth_method_distribution", log_type, [])

    def fetch_client_software_distribution(self, start_time, end_time, limit, log_type="vpn"):
        self.calls.append(("fetch_client_software_distribution", limit, log_type))
        return []

    def fetch_protocol_distribution(self, start_time, end_time, log_type="vpn"):
        return self._record("fetch_protocol_distribution", log_type, [])

    def fetch_daily_event_counts(self, start_time, end_time, log_type="vpn"):
        return self._record("fetch_daily_event_counts", log_type, [])

    def fetch_session_metric_summary(self, start_time, end_time, log_type="vpn"):
        return self._record("fetch_session_metric_summary", log_type, [])


class FakeMerger:
    """记录 merge 调用。"""

    def __init__(self):
        self.called = False

    def merge(self, **kwargs):
        self.called = True
        return {"zhangsan": UserAggregateFeature(username="zhangsan", sample_count=20)}


class FakeBuilder:
    """返回两条 Baseline 以验证统计。"""

    def __init__(self):
        self.called = False

    def build_baselines(self, features_by_user, start_time, end_time):
        self.called = True
        return [
            _baseline("zhangsan", 20, True, start_time, end_time),
            _baseline("lisi", 1, False, start_time, end_time),
        ]


class FakeStore:
    """记录 Store 调用。"""

    def __init__(self):
        self.ensure_called = False
        self.saved = []

    def ensure_table(self):
        self.ensure_called = True

    def save_baselines(self, baselines):
        self.saved.extend(baselines)
        return len(baselines)


def _baseline(username, sample_count, reliable, start_time, end_time):
    """构造 Service 测试 Baseline。"""
    return UserBaseline(
        username=username,
        sample_count=sample_count,
        is_reliable=reliable,
        failed_rate=0.0,
        off_hours_rate=0.0,
        unusual_ip_rate=0.0,
        avg_daily_events=float(sample_count),
        active_day_avg_events=float(sample_count),
        max_daily_events=sample_count,
        baseline_start_time=start_time,
        baseline_end_time=end_time,
        model_version="ueba_baseline_v1",
    )


def _service(repository=None, merger=None, builder=None, store=None):
    """构造带 fake 组件的 Service。"""
    return UebaService(
        repository=repository or FakeRepository(),
        aggregate_merger=merger or FakeMerger(),
        baseline_builder=builder or FakeBuilder(),
        baseline_store=store or FakeStore(),
        config=UebaBaselineConfig(),
    )


def test_build_baseline_once_success_path():
    """验证 Service 正常编排并返回统计。"""
    repository = FakeRepository()
    merger = FakeMerger()
    builder = FakeBuilder()
    store = FakeStore()
    result = _service(repository, merger, builder, store).build_baseline_once(
        datetime(2026, 5, 1),
        datetime(2026, 5, 6),
        log_type="vpn",
    )

    assert isinstance(result, BaselineBuildResult)
    assert result.success is True
    assert store.ensure_called is True
    assert merger.called is True
    assert builder.called is True
    assert len(store.saved) == 2
    assert result.total_user_count == 2
    assert result.reliable_user_count == 1
    assert result.unreliable_user_count == 1
    assert result.total_log_count == 21
    assert result.message == "saved 2 user baselines"
    assert any(call[-1] == "vpn" for call in repository.calls)
    expected_names = {
        "fetch_user_summary",
        "fetch_hour_distribution",
        "fetch_top_source_ips",
        "fetch_top_destination_ips",
        "fetch_top_source_countries",
        "fetch_top_source_cities",
        "fetch_top_vpn_gateways",
        "fetch_action_distribution",
        "fetch_event_type_distribution",
        "fetch_result_distribution",
        "fetch_fail_reason_distribution",
        "fetch_auth_method_distribution",
        "fetch_client_software_distribution",
        "fetch_protocol_distribution",
        "fetch_daily_event_counts",
        "fetch_session_metric_summary",
    }
    assert expected_names.issubset({call[0] for call in repository.calls})


def test_build_baseline_once_invalid_time_window_returns_failure():
    """验证非法时间窗口返回失败结果。"""
    result = _service().build_baseline_once(datetime(2026, 5, 6), datetime(2026, 5, 1))

    assert result.success is False
    assert result.total_user_count == 0
    assert "时间" in result.message


def test_repository_exception_returns_failure():
    """验证 Repository 异常不会返回假成功。"""
    result = _service(repository=FakeRepository(fail=True)).build_baseline_once(
        datetime(2026, 5, 1),
        datetime(2026, 5, 6),
    )

    assert result.success is False
    assert result.total_log_count == 0
    assert "RuntimeError" in result.message
