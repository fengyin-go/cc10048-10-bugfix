"""监控交付场景测试：导出批次交付资格、领取回执、到账补到与原批重处理。

完整业务剧本：
1. 值班员一次提交到账、退款、混合三份资料：到账小时表迟到、退款日表及时；
2. 退款组实际领取退款明细；混合明细与到账明细不能领取，批次须告知收件组；
3. 到账数据补到后对原批重新提交（再次处理）两次；
4. 核对各明细资格、份数、真实来源数据与实际回执；
5. 已交付的退款结果不能重复签收或变更领取凭据。
"""
from datetime import datetime, timedelta

import pytest

from app.services.delivery import MATERIAL_COPIES, delivery_service
from app.services.data_freshness import DataFreshnessService
from app.store import store


@pytest.fixture(autouse=True)
def reset_delivery():
    delivery_service.reset()
    # 保证每个用例开始时到账小时表处于迟到、退款日表处于及时的初始现场
    freshness = DataFreshnessService()
    freshness.backfill_arrival(
        4, (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")
    )
    freshness.backfill_arrival(
        5, (datetime.now() - timedelta(minutes=48)).strftime("%Y-%m-%dT%H:%M:%S")
    )
    yield
    delivery_service.reset()


class TestDeliveryEligibility:
    def test_refund_eligible_arrival_late(self):
        refund = delivery_service.evaluate_material("退款明细")
        assert refund["eligible"] is True
        assert refund["required_sources"] == ["退款日表"]

        arrival = delivery_service.evaluate_material("到账明细")
        assert arrival["eligible"] is False
        assert arrival["blocked_reasons"][0]["source"] == "到账小时表"

    def test_mixed_requires_both_sources(self):
        mixed = delivery_service.evaluate_material("混合明细")
        assert mixed["required_sources"] == ["到账小时表", "退款日表"]
        assert mixed["eligible"] is False
        blocked_sources = {item["source"] for item in mixed["blocked_reasons"]}
        assert "到账小时表" in blocked_sources

    def test_unknown_material(self):
        with pytest.raises(ValueError):
            delivery_service.evaluate_material("不存在的明细")

    def test_source_status_contains_real_arrival_time(self):
        statuses = delivery_service.source_statuses()
        assert statuses["退款日表"]["actual_arrival_time"] is not None
        assert statuses["退款日表"]["expected_text"] == "1.5小时"
        assert statuses["到账小时表"]["expected_text"] == "2小时"


class TestBatchSubmitAndClaim:
    def test_submit_three_materials_partial_delivery(self):
        batch = delivery_service.submit_batch(
            ["到账明细", "退款明细", "混合明细"], "退款组"
        )
        assert batch["status"] == "部分交付"
        materials = {item["material_type"]: item for item in batch["materials"]}

        # 仅退款明细具备交付资格
        assert materials["退款明细"]["eligible"] is True
        assert materials["到账明细"]["eligible"] is False
        assert materials["混合明细"]["eligible"] is False

        # 缺来源的材料必须在批次结果里告知收件组
        notice_text = "".join(batch["notices"])
        assert "到账小时表" in notice_text
        assert "混合导出明细" in notice_text or "混合明细" in notice_text

    def test_refund_team_claims_refund_material(self):
        batch = delivery_service.submit_batch(
            ["到账明细", "退款明细", "混合明细"], "退款组"
        )
        batch_id = batch["batch_id"]
        claim = delivery_service.claim_material(batch_id, "退款明细", "退款组")
        assert claim["ok"] is True
        assert claim["first_claim"] is True
        receipt = claim["receipt"]
        assert receipt["claimed"] is True
        assert receipt["copies"] == MATERIAL_COPIES["退款明细"]
        assert receipt["claim_token"].startswith("RCV-")
        # 回执记录采用的真实来源数据与实际到数时间
        source_data = {item["source"]: item for item in receipt["source_data"]}
        assert "退款日表" in source_data
        assert source_data["退款日表"]["actual_arrival_time"] is not None
        assert source_data["退款日表"]["expected_text"] == "1.5小时"

    def test_blocked_material_cannot_be_claimed(self):
        batch = delivery_service.submit_batch(
            ["到账明细", "退款明细", "混合明细"], "退款组"
        )
        result = delivery_service.claim_material(batch["batch_id"], "混合明细", "退款组")
        assert "error" in result
        result2 = delivery_service.claim_material(batch["batch_id"], "到账明细", "退款组")
        assert "error" in result2

    def test_duplicate_claim_keeps_same_receipt(self):
        batch = delivery_service.submit_batch(["退款明细"], "退款组")
        batch_id = batch["batch_id"]
        first = delivery_service.claim_material(batch_id, "退款明细", "退款组")
        second = delivery_service.claim_material(batch_id, "退款明细", "退款组")
        assert second["duplicate"] is True
        assert second["first_claim"] is False
        assert second["receipt"]["receipt_id"] == first["receipt"]["receipt_id"]
        assert second["receipt"]["claim_token"] == first["receipt"]["claim_token"]
        assert second["receipt"]["claimed_at"] == first["receipt"]["claimed_at"]


class TestBackfillAndReprocess:
    def _initial_batch(self):
        return delivery_service.submit_batch(
            ["到账明细", "退款明细", "混合明细"], "退款组"
        )

    def test_full_scenario_backfill_then_reprocess_twice(self):
        batch = self._initial_batch()
        batch_id = batch["batch_id"]

        # 退款组先实际领取退款明细
        refund_claim = delivery_service.claim_material(batch_id, "退款明细", "退款组")
        refund_receipt_id = refund_claim["receipt"]["receipt_id"]
        refund_token = refund_claim["receipt"]["claim_token"]

        # 到账数据补到：到账小时表实际到数时间更新为 20 分钟前（2小时时效内）
        fresh_arrival = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S")
        backfill = delivery_service.backfill_source("到账小时表", fresh_arrival)
        assert "error" not in backfill
        assert backfill["result"]["status"] == "及时"
        assert backfill["expected_text"] == "2小时"
        assert backfill["actual_arrival_time"] is not None

        # 第一次重新处理原批：到账明细与混合明细补发，各自拿到新回执
        reprocessed = delivery_service.reprocess_batch(batch_id)
        assert reprocessed["newly_delivered"]
        new_types = {item["material_type"] for item in reprocessed["newly_delivered"]}
        assert new_types == {"到账明细", "混合明细"}
        assert reprocessed["status"] == "已齐套可交付"

        materials = {item["material_type"]: item for item in reprocessed["materials"]}
        assert materials["到账明细"]["eligible"] is True
        assert materials["混合明细"]["eligible"] is True
        # 退款明细原回执保持不变
        assert materials["退款明细"]["receipt_id"] == refund_receipt_id

        # 第二次重新处理：没有新材料补发，已交付凭据不变
        again = delivery_service.reprocess_batch(batch_id)
        assert again["newly_delivered"] == []
        assert again["reprocess_count"] == 2
        assert {item["material_type"] for item in again["materials"]} == {
            "到账明细", "退款明细", "混合明细"
        }

        # 退款结果重复签收：返回原回执，凭据未变更
        duplicate = delivery_service.claim_material(batch_id, "退款明细", "退款组")
        assert duplicate["duplicate"] is True
        assert duplicate["receipt"]["receipt_id"] == refund_receipt_id
        assert duplicate["receipt"]["claim_token"] == refund_token

        # 到账明细与混合明细现在可以被实际领取
        claim_arrival = delivery_service.claim_material(batch_id, "到账明细", "退款组")
        claim_mixed = delivery_service.claim_material(batch_id, "混合明细", "退款组")
        assert claim_arrival["first_claim"] is True
        assert claim_mixed["first_claim"] is True
        assert claim_mixed["receipt"]["copies"] == MATERIAL_COPIES["混合明细"]

        # 接收方通过回执清单可查到全部份数与真实来源数据
        receipts = delivery_service.list_receipts()
        assert len(receipts) == 3
        mixed_receipt = next(
            r for r in receipts if r["material_type"] == "混合明细"
        )
        mixed_sources = {s["source"] for s in mixed_receipt["source_data"]}
        assert mixed_sources == {"到账小时表", "退款日表"}
        assert all(s["actual_arrival_time"] for s in mixed_receipt["source_data"])

        # 批次历史可追溯：首次提交 → 退款签收 → 两次重处理 → 后续签收
        actions = [event["action"] for event in again["history"]]
        assert "首次提交" in actions
        assert sum(1 for a in actions if "原批再次处理" in a) == 2

    def test_reprocess_unknown_batch(self):
        result = delivery_service.reprocess_batch(99999)
        assert "error" in result

    def test_backfill_unknown_source(self):
        result = delivery_service.backfill_source("不存在来源", "2026-11-25T10:00:00")
        assert "error" in result

    def test_reprocess_before_backfill_delivers_nothing(self):
        batch = self._initial_batch()
        result = delivery_service.reprocess_batch(batch["batch_id"])
        assert result["newly_delivered"] == []
        assert result["status"] == "部分交付"
