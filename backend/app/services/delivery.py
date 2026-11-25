"""监控交付领域服务：按来源实际时效计算导出批次交付资格并管理领取凭据。

业务规则（题面口径）：
- 交付资格严格按到账数据的实际时效判定，时效口径与数据时效模块共用同一解析器；
- 到账明细只依赖到账小时表，退款明细只依赖退款日表，混合导出必须两张源表都及时；
- 任一来源缺失/迟到，对应材料不能领取，并须在批次结果中告知收件组；
- 领取动作幂等：同一接收方对同一材料只能签收一次，重复领取返回原回执，
  已交付材料的领取凭据在批次重处理后保持不变；
- 到账数据补到后可对原批再次处理，只补发新满足资格的材料，不重复签收已交付结果。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.duration import format_hours
from app.services.data_freshness import DataFreshnessService

# 材料类型 → 依赖的数据来源（数据时效模块中的「数据来源」名称）
MATERIAL_SOURCES: dict[str, list[str]] = {
    "到账明细": ["到账小时表"],
    "退款明细": ["退款日表"],
    "混合明细": ["到账小时表", "退款日表"],
}

# 每种材料的固定份数（接收方可查到实际派发份数）
MATERIAL_COPIES: dict[str, int] = {
    "到账明细": 1,
    "退款明细": 1,
    "混合明细": 2,
}

MATERIAL_LABELS: dict[str, str] = {
    "到账明细": "到账明细",
    "退款明细": "退款明细",
    "混合明细": "混合导出明细",
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DeliveryService:
    """导出批次交付资格与领取凭据服务（进程内存储，与内存仓库保持一致）。"""

    def __init__(self, freshness_service: DataFreshnessService | None = None) -> None:
        self._freshness = freshness_service or DataFreshnessService()
        # 可重入锁：批次/领取方法持锁时会复用同样加锁的查询方法
        self._lock = threading.RLock()
        self._batches: dict[int, dict[str, Any]] = {}
        self._receipts: dict[int, dict[str, Any]] = {}
        self._batch_seq = 0
        self._receipt_seq = 0

    def reset(self) -> None:
        """清空批次与回执（测试隔离用）。"""
        with self._lock:
            self._batches.clear()
            self._receipts.clear()
            self._batch_seq = 0
            self._receipt_seq = 0

    # ---- 来源时效与交付资格 ----
    def evaluate_sources(self, *, current_time: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """评估两张源表的实际时效状态。"""
        return {
            source: [self._freshness.evaluate_source(source, current_time=current_time)]
            for source in ("到账小时表", "退款日表")
        }

    def source_statuses(self, *, current_time: str | None = None) -> dict[str, dict[str, Any]]:
        return {
            source: self._freshness.evaluate_source(source, current_time=current_time)
            for source in ("到账小时表", "退款日表")
        }

    def evaluate_material(
        self, material_type: str, *, current_time: str | None = None
    ) -> dict[str, Any]:
        """计算单个导出材料的交付资格及其采用的真实来源数据。"""
        if material_type not in MATERIAL_SOURCES:
            raise ValueError(f"未知材料类型：{material_type}，可选 {list(MATERIAL_SOURCES)}")
        required_sources = MATERIAL_SOURCES[material_type]
        source_evals = [
            self._freshness.evaluate_source(source, current_time=current_time)
            for source in required_sources
        ]
        eligible = all(item["eligible"] for item in source_evals)
        blocking = [
            {
                "source": item["source"],
                "status": item["status"],
                "actual_arrival_time": item.get("actual_arrival_time"),
                "expected_text": item.get("expected_text"),
                "message": item["message"],
            }
            for item in source_evals
            if not item["eligible"]
        ]
        return {
            "material_type": material_type,
            "material_name": MATERIAL_LABELS[material_type],
            "copies": MATERIAL_COPIES[material_type],
            "required_sources": required_sources,
            "eligible": eligible,
            "blocked_reasons": blocking,
            "sources": source_evals,
        }

    # ---- 批次提交 ----
    def submit_batch(
        self,
        material_types: list[str],
        receiver: str,
        *,
        current_time: str | None = None,
    ) -> dict[str, Any]:
        """一次提交多份材料，按各来源实际时效生成交付批次。"""
        if not material_types:
            raise ValueError("至少选择一种材料")
        receiver = (receiver or "").strip() or "未指定收件组"
        materials = [self.evaluate_material(mt, current_time=current_time) for mt in material_types]
        with self._lock:
            self._batch_seq += 1
            batch_id = self._batch_seq
            submitted_at = _now_text()
            items = []
            for index, material in enumerate(materials, start=1):
                item = {
                    "item_no": index,
                    "material_type": material["material_type"],
                    "material_name": material["material_name"],
                    "copies": material["copies"],
                    "eligible": material["eligible"],
                    "required_sources": material["required_sources"],
                    "sources": material["sources"],
                    "blocked_reasons": material["blocked_reasons"],
                    "receipt_id": None,
                    "delivered": False,
                }
                if material["eligible"]:
                    self._receipt_seq += 1
                    receipt_id = self._receipt_seq
                    receipt = {
                        "receipt_id": receipt_id,
                        "batch_id": batch_id,
                        "material_type": material["material_type"],
                        "material_name": material["material_name"],
                        "receiver": receiver,
                        "copies": material["copies"],
                        "source_data": [
                            {
                                "source": src["source"],
                                "expected_text": src.get("expected_text"),
                                "expected_hours": src.get("expected_hours"),
                                "actual_arrival_time": src.get("actual_arrival_time"),
                                "actual_delay_hours": src.get("actual_delay_hours"),
                                "status": src["status"],
                            }
                            for src in material["sources"]
                        ],
                        "issued_at": submitted_at,
                        "claimed": False,
                        "claimed_at": None,
                        "claim_token": f"RCV-{batch_id:04d}-{receipt_id:04d}",
                    }
                    self._receipts[receipt_id] = receipt
                    item["receipt_id"] = receipt_id
                    item["claim_token"] = receipt["claim_token"]
                items.append(item)

            blocked_items = [item for item in items if not item["eligible"]]
            delivered_items = [item for item in items if item["eligible"]]
            if blocked_items and delivered_items:
                status = "部分交付"
            elif blocked_items:
                status = "待齐套"
            else:
                status = "已交付"
            notices = []
            for item in blocked_items:
                source_names = "、".join(
                    reason["source"] for reason in item["blocked_reasons"]
                )
                notices.append(
                    f"{item['material_name']}因来源 {source_names} 未满足时效，本批暂不能领取，"
                    f"数据补齐后可对原批（批次号 {batch_id}）再次处理"
                )
            batch = {
                "batch_id": batch_id,
                "receiver": receiver,
                "submitted_at": submitted_at,
                "status": status,
                "materials": items,
                "notices": notices,
                "reprocess_count": 0,
                "history": [{"at": submitted_at, "action": "首次提交", "status": status}],
            }
            self._batches[batch_id] = batch
        return self.get_batch(batch_id)

    # ---- 领取 ----
    def claim_material(
        self, batch_id: int, material_type: str, receiver: str
    ) -> dict[str, Any]:
        """接收方实际领取材料；幂等，重复签收返回原回执且不变更凭据。"""
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return {"error": f"交付批次 {batch_id} 不存在"}
            item = next(
                (row for row in batch["materials"] if row["material_type"] == material_type),
                None,
            )
            if item is None:
                return {"error": f"批次 {batch_id} 中不存在材料 {material_type}"}
            if not item["eligible"] or item["receipt_id"] is None:
                return {
                    "error": f"{item['material_name']}当前不具备交付资格，无法领取",
                    "blocked_reasons": item["blocked_reasons"],
                }
            receipt = self._receipts[item["receipt_id"]]
            if not receipt["claimed"]:
                receipt["claimed"] = True
                receipt["claimed_at"] = _now_text()
                receipt["claim_receiver"] = (receiver or "").strip() or batch["receiver"]
                item["delivered"] = True
                batch["history"].append(
                    {"at": _now_text(), "action": f"{material_type}签收", "status": batch["status"]}
                )
                first_claim = True
            else:
                first_claim = False
            return {
                "ok": True,
                "first_claim": first_claim,
                "duplicate": not first_claim,
                "message": "签收成功" if first_claim else "该材料已签收，返回原回执，凭据未变更",
                "receipt": dict(receipt),
            }

    # ---- 到账补到后的原批重处理 ----
    def backfill_source(self, source_name: str, arrival_time: str) -> dict[str, Any]:
        """来源数据补到：定位时效检查并更新实际到数时间。"""
        from app.store import store

        matched = [
            row for row in store.rows(self._freshness.MODULE)
            if str(row.get("数据来源", "")).strip() == str(source_name).strip()
        ]
        if not matched:
            return {"error": f"数据来源 {source_name} 未配置时效检查"}
        outcome = self._freshness.backfill_arrival(int(matched[0]["id"]), arrival_time)
        return outcome

    def reprocess_batch(
        self, batch_id: int, *, current_time: str | None = None
    ) -> dict[str, Any]:
        """到账补到后对原批再次处理：只补发新满足资格的材料，已交付回执不变。"""
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return {"error": f"交付批次 {batch_id} 不存在"}
            reprocessed_at = _now_text()
            newly_delivered: list[dict[str, Any]] = []
            for item in batch["materials"]:
                if item["eligible"]:
                    continue  # 已具备资格（含已签收）的材料保持原回执不变
                material = self.evaluate_material(
                    item["material_type"], current_time=current_time
                )
                item["sources"] = material["sources"]
                item["blocked_reasons"] = material["blocked_reasons"]
                if material["eligible"]:
                    self._receipt_seq += 1
                    receipt_id = self._receipt_seq
                    receipt = {
                        "receipt_id": receipt_id,
                        "batch_id": batch_id,
                        "material_type": material["material_type"],
                        "material_name": material["material_name"],
                        "receiver": batch["receiver"],
                        "copies": material["copies"],
                        "source_data": [
                            {
                                "source": src["source"],
                                "expected_text": src.get("expected_text"),
                                "expected_hours": src.get("expected_hours"),
                                "actual_arrival_time": src.get("actual_arrival_time"),
                                "actual_delay_hours": src.get("actual_delay_hours"),
                                "status": src["status"],
                            }
                            for src in material["sources"]
                        ],
                        "issued_at": reprocessed_at,
                        "claimed": False,
                        "claimed_at": None,
                        "claim_token": f"RCV-{batch_id:04d}-{receipt_id:04d}",
                    }
                    self._receipts[receipt_id] = receipt
                    item["eligible"] = True
                    item["receipt_id"] = receipt_id
                    item["claim_token"] = receipt["claim_token"]
                    item["blocked_reasons"] = []
                    newly_delivered.append(
                        {"material_type": item["material_type"], "receipt_id": receipt_id}
                    )
            blocked_items = [row for row in batch["materials"] if not row["eligible"]]
            delivered_items = [row for row in batch["materials"] if row["eligible"]]
            if blocked_items and delivered_items:
                batch["status"] = "部分交付"
            elif blocked_items:
                batch["status"] = "待齐套"
            else:
                batch["status"] = "已齐套可交付"
            batch["reprocess_count"] += 1
            batch["notices"] = [
                f"{row['material_name']}因来源 "
                + "、".join(reason["source"] for reason in row["blocked_reasons"])
                + " 未满足时效，仍暂不能领取"
                for row in blocked_items
            ]
            batch["history"].append(
                {
                    "at": reprocessed_at,
                    "action": f"原批再次处理（第{batch['reprocess_count']}次）",
                    "status": batch["status"],
                    "newly_delivered": newly_delivered,
                }
            )
            result = self.get_batch(batch_id)
            result["newly_delivered"] = newly_delivered
            return result

    # ---- 查询 ----
    def get_batch(self, batch_id: int) -> dict[str, Any]:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return {"error": f"交付批次 {batch_id} 不存在"}
            snapshot = dict(batch)
            snapshot["materials"] = [dict(item) for item in batch["materials"]]
            snapshot["history"] = [dict(event) for event in batch["history"]]
            snapshot["materials_view"] = [
                self._material_view(item) for item in snapshot["materials"]
            ]
            return snapshot

    def _material_view(self, item: dict[str, Any]) -> dict[str, Any]:
        """接收方可查的材料视图：份数、真实来源数据与领取凭据。"""
        view = {
            "material_type": item["material_type"],
            "material_name": item["material_name"],
            "copies": item["copies"],
            "eligible": item["eligible"],
            "delivered": item["delivered"],
            "required_sources": item["required_sources"],
            "blocked_reasons": item["blocked_reasons"],
        }
        if item.get("receipt_id") is not None:
            receipt = self._receipts[item["receipt_id"]]
            view["receipt"] = dict(receipt)
        return view

    def list_batches(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self.get_batch(batch_id) for batch_id in sorted(self._batches)]

    def get_receipt(self, receipt_id: int) -> dict[str, Any]:
        with self._lock:
            receipt = self._receipts.get(receipt_id)
            if receipt is None:
                return {"error": f"领取回执 {receipt_id} 不存在"}
            return dict(receipt)

    def list_receipts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(receipt) for receipt in self._receipts.values()]


# 单例：与 SERVICES 中的时效服务共用同一服务实例，保证两处口径绝对一致
delivery_service = DeliveryService()
