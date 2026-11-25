"""监控交付接口：导出批次交付资格、资料领取回执、到账补到与原批重处理。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.delivery import MATERIAL_SOURCES, delivery_service

router = APIRouter(prefix="/api/delivery", tags=["监控交付"])


class BatchSubmitPayload(BaseModel):
    materials: list[str] = Field(default_factory=list)
    receiver: str = ""


class ClaimPayload(BaseModel):
    receiver: str = ""


class BackfillPayload(BaseModel):
    source: str
    arrival_time: str


@router.get("/materials")
def list_materials() -> dict[str, Any]:
    """材料目录及其依赖来源、份数。"""
    from app.services.delivery import MATERIAL_COPIES, MATERIAL_LABELS

    return {
        "materials": [
            {
                "material_type": key,
                "material_name": MATERIAL_LABELS[key],
                "required_sources": sources,
                "copies": MATERIAL_COPIES[key],
            }
            for key, sources in MATERIAL_SOURCES.items()
        ]
    }


@router.get("/sources")
def source_statuses() -> dict[str, Any]:
    """两张源表的实际时效与实际到数时间。"""
    return {"sources": delivery_service.source_statuses()}


@router.get("/eligibility/{material_type}")
def material_eligibility(material_type: str) -> dict[str, Any]:
    """单个材料的导出批次交付资格。"""
    try:
        return delivery_service.evaluate_material(material_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batches")
def submit_batch(payload: BatchSubmitPayload) -> dict[str, Any]:
    """值班员一次提交多份资料（到账/退款/混合明细）。"""
    try:
        return delivery_service.submit_batch(payload.materials, payload.receiver)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/batches")
def list_batches() -> dict[str, Any]:
    return {"batches": delivery_service.list_batches()}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int) -> dict[str, Any]:
    result = delivery_service.get_batch(batch_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/batches/{batch_id}/claim/{material_type}")
def claim_material(batch_id: int, material_type: str, payload: ClaimPayload) -> dict[str, Any]:
    """接收方实际领取材料；幂等，重复签收返回原回执。"""
    result = delivery_service.claim_material(batch_id, material_type, payload.receiver)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/backfill")
def backfill_source(payload: BackfillPayload) -> dict[str, Any]:
    """到账数据补到：登记来源实际到数时间并重新判定时效。"""
    result = delivery_service.backfill_source(payload.source, payload.arrival_time)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/batches/{batch_id}/reprocess")
def reprocess_batch(batch_id: int) -> dict[str, Any]:
    """补到后对原批再次处理：只补发新满足资格的材料，已交付凭据不变。"""
    result = delivery_service.reprocess_batch(batch_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/receipts")
def list_receipts() -> dict[str, Any]:
    """接收方查询全部领取回执（份数与真实来源数据）。"""
    return {"receipts": delivery_service.list_receipts()}


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: int) -> dict[str, Any]:
    result = delivery_service.get_receipt(receipt_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
