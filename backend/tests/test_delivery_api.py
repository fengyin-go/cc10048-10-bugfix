"""监控交付接口与监控交付链路的 HTTP 集成测试。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.delivery import delivery_service
from app.services.data_freshness import DataFreshnessService
from app.store import store

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    delivery_service.reset()
    freshness = DataFreshnessService()
    freshness.backfill_arrival(
        4, (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")
    )
    freshness.backfill_arrival(
        5, (datetime.now() - timedelta(minutes=48)).strftime("%Y-%m-%dT%H:%M:%S")
    )
    yield
    delivery_service.reset()


class TestFreshnessEndpoints:
    def test_decimal_hours_check(self):
        resp = client.get("/api/domain/data_freshness/2/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["expected_hours"] == 1.5
        assert data["expected_text"] == "1.5小时"
        assert data["actual_arrival_time"] is not None

    def test_chinese_unit_check(self):
        resp = client.get("/api/domain/data_freshness/3/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["expected_hours"] == 2.0
        assert data["expected_text"] == "2小时"

    def test_monitor_chain_uses_same_parser(self):
        resp = client.get("/api/chains/monitor-delivery/1")
        assert resp.status_code == 200
        data = resp.json()
        by_source = {item["source"]: item for item in data["data_freshness"]}
        assert by_source["库存快照表"]["expected_hours"] == 2.0
        assert by_source["用户行为表"]["expected_hours"] == 1.5
        assert by_source["到账小时表"]["actual_arrival_time"] is not None
        eligibility = data["delivery_eligibility"]
        assert eligibility["退款明细"]["eligible"] is True
        assert eligibility["到账明细"]["eligible"] is False
        assert eligibility["混合明细"]["eligible"] is False


class TestDeliveryEndpoints:
    def test_materials_catalog(self):
        resp = client.get("/api/delivery/materials")
        assert resp.status_code == 200
        types = {item["material_type"] for item in resp.json()["materials"]}
        assert types == {"到账明细", "退款明细", "混合明细"}

    def test_sources_endpoint(self):
        resp = client.get("/api/delivery/sources")
        assert resp.status_code == 200
        sources = resp.json()["sources"]
        assert sources["退款日表"]["eligible"] is True
        assert sources["到账小时表"]["eligible"] is False

    def test_full_delivery_flow_over_http(self):
        # 一次提交三份资料
        resp = client.post("/api/delivery/batches", json={
            "materials": ["到账明细", "退款明细", "混合明细"],
            "receiver": "退款组",
        })
        assert resp.status_code == 200
        batch = resp.json()
        batch_id = batch["batch_id"]
        assert batch["status"] == "部分交付"

        # 退款组实际领取退款明细
        claim = client.post(
            f"/api/delivery/batches/{batch_id}/claim/退款明细",
            json={"receiver": "退款组"},
        )
        assert claim.status_code == 200
        assert claim.json()["first_claim"] is True

        # 混合明细缺到账来源，不能领取
        blocked = client.post(
            f"/api/delivery/batches/{batch_id}/claim/混合明细",
            json={"receiver": "退款组"},
        )
        assert blocked.status_code == 400

        # 到账补到
        arrival = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S")
        backfill = client.post("/api/delivery/backfill", json={
            "source": "到账小时表", "arrival_time": arrival,
        })
        assert backfill.status_code == 200
        assert backfill.json()["result"]["status"] == "及时"

        # 重新处理原批两次
        first = client.post(f"/api/delivery/batches/{batch_id}/reprocess")
        assert first.status_code == 200
        assert {item["material_type"] for item in first.json()["newly_delivered"]} == {
            "到账明细", "混合明细",
        }
        second = client.post(f"/api/delivery/batches/{batch_id}/reprocess")
        assert second.json()["newly_delivered"] == []

        # 重复签收退款明细：凭据不变
        dup = client.post(
            f"/api/delivery/batches/{batch_id}/claim/退款明细",
            json={"receiver": "退款组"},
        )
        assert dup.json()["duplicate"] is True

        # 回执清单可查份数与真实来源
        receipts = client.get("/api/delivery/receipts").json()["receipts"]
        assert len(receipts) == 3
        mixed = next(r for r in receipts if r["material_type"] == "混合明细")
        assert {s["source"] for s in mixed["source_data"]} == {
            "到账小时表", "退款日表",
        }

        # 批次查询
        detail = client.get(f"/api/delivery/batches/{batch_id}")
        assert detail.status_code == 200
        assert detail.json()["reprocess_count"] == 2

    def test_claim_nonexistent_batch(self):
        resp = client.post(
            "/api/delivery/batches/99999/claim/退款明细", json={"receiver": "退款组"}
        )
        assert resp.status_code == 400

    def test_get_nonexistent_receipt(self):
        resp = client.get("/api/delivery/receipts/99999")
        assert resp.status_code == 404
