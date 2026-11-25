"""时效口径解析与时效检查测试：单独检查入口与监控交付共用同一口径。"""
import pytest

from app.duration import format_hours, parse_duration_hours, try_parse_duration_hours
from app.services.data_freshness import DataFreshnessService
from app.store import store


class TestDurationParser:
    """两处时效校验必须读出同一小时数。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1.5h", 1.5),
            ("1.5小时", 1.5),
            ("2小时", 2.0),
            ("2h", 2.0),
            ("2", 2.0),
            ("24小时", 24.0),
            ("90分钟", 1.5),
            (2, 2.0),
            (1.5, 1.5),
        ],
    )
    def test_parse_duration(self, raw, expected):
        assert try_parse_duration_hours(raw) == expected
        assert parse_duration_hours(raw) == expected

    def test_invalid_returns_none_strict(self):
        assert try_parse_duration_hours("数据时效样例1") is None
        assert try_parse_duration_hours("") is None
        assert try_parse_duration_hours(None) is None

    def test_invalid_default_fallback(self):
        assert parse_duration_hours("无法解析", default=24.0) == 24.0
        with pytest.raises(ValueError):
            parse_duration_hours("无法解析", default=None)

    def test_format_hours(self):
        assert format_hours(2.0) == "2小时"
        assert format_hours(1.5) == "1.5小时"
        assert format_hours(24.0) == "24小时"


class TestFreshnessService:
    def setup_method(self):
        self.svc = DataFreshnessService()

    def test_decimal_hours_no_crash(self):
        """历史缺陷：1.5h 在单独检查入口 int() 直接报错。"""
        row = store.find("data_freshness", 2)  # 用户行为表，期望时效 1.5h
        assert try_parse_duration_hours(row["期望时效"]) == 1.5
        result = self.svc.run_freshness_check(2)
        assert "error" not in result
        assert result["expected_hours"] == 1.5
        assert result["expected_text"] == "1.5小时"
        assert result["result"]["actual_arrival_time"] is not None

    def test_chinese_unit_read_as_two_hours(self):
        """历史缺陷：2小时 在监控交付被静默读成 24 小时。"""
        row = store.find("data_freshness", 3)  # 库存快照表，期望时效 2小时
        assert try_parse_duration_hours(row["期望时效"]) == 2.0
        result = self.svc.run_freshness_check(3)
        assert result["expected_hours"] == 2.0
        assert result["expected_text"] == "2小时"

    def test_invalid_expected_returns_structured_error(self):
        created, missing = self.svc.create_entry({
            "检查标识": "FRESH-BAD",
            "数据来源": "坏配置来源",
            "期望时效": "无法解析的时效",
        })
        assert missing == []
        result = self.svc.run_freshness_check(int(created["id"]))
        assert "error" in result
        assert "无法解析" in result["error"]

    def test_check_shows_actual_arrival_time(self):
        result = self.svc.run_freshness_check(5)  # 退款日表
        assert result["actual_arrival_time"] is not None
        stored = store.find("data_freshness", 5)
        assert stored["实际到数时间"] == result["actual_arrival_time"]

    def test_evaluate_source_timely_and_late(self):
        refund = self.svc.evaluate_source("退款日表")
        assert refund["eligible"] is True
        assert refund["status"] == "及时"
        arrival = self.svc.evaluate_source("到账小时表")
        assert arrival["eligible"] is False
        assert arrival["status"] in ("延迟", "严重延迟")
        assert arrival["actual_arrival_time"] is not None

    def test_evaluate_unknown_source(self):
        result = self.svc.evaluate_source("不存在的来源")
        assert result["eligible"] is False
        assert result["configured"] is False

    def test_backfill_arrival_makes_source_timely(self):
        from datetime import datetime, timedelta

        fresh_time = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S")
        outcome = self.svc.backfill_arrival(4, fresh_time)
        assert "error" not in outcome
        assert outcome["result"]["status"] == "及时"
        # 恢复迟到现场，避免污染其他测试
        self.svc.backfill_arrival(
            4, (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")
        )

    def test_backfill_bad_time(self):
        outcome = self.svc.backfill_arrival(4, "not-a-time")
        assert "error" in outcome
