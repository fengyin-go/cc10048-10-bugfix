import { request } from './client'

export type SourceStatus = {
  source: string
  configured: boolean
  eligible: boolean
  expected_hours: number | null
  expected_text: string | null
  expected_raw?: string | null
  status: string
  actual_delay_hours: number | null
  actual_arrival_time: string | null
  checked_at?: string
  message: string
}

export type MaterialCatalog = {
  material_type: string
  material_name: string
  required_sources: string[]
  copies: number
}

export type Receipt = {
  receipt_id: number
  batch_id: number
  material_type: string
  material_name: string
  receiver: string
  copies: number
  source_data: {
    source: string
    expected_text: string | null
    expected_hours: number | null
    actual_arrival_time: string | null
    actual_delay_hours: number | null
    status: string
  }[]
  issued_at: string
  claimed: boolean
  claimed_at: string | null
  claim_token: string
  claim_receiver?: string
}

export type BatchMaterial = {
  item_no: number
  material_type: string
  material_name: string
  copies: number
  eligible: boolean
  delivered: boolean
  required_sources: string[]
  blocked_reasons: { source: string; status: string; message: string }[]
  receipt_id: number | null
  claim_token?: string
  receipt?: Receipt
}

export type Batch = {
  batch_id: number
  receiver: string
  submitted_at: string
  status: string
  materials: BatchMaterial[]
  notices: string[]
  reprocess_count: number
  history: { at: string; action: string; status: string }[]
  newly_delivered?: { material_type: string; receipt_id: number }[]
  materials_view?: unknown[]
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail
    throw new Error(typeof detail === 'string' ? detail : '请求失败')
  }
  return (await response.json()) as T
}

export function fetchSourceStatuses(): Promise<{ sources: Record<string, SourceStatus> }> {
  return jsonRequest('/api/delivery/sources')
}

export function fetchMaterials(): Promise<{ materials: MaterialCatalog[] }> {
  return jsonRequest('/api/delivery/materials')
}

export function submitBatch(materials: string[], receiver: string): Promise<Batch> {
  return jsonRequest('/api/delivery/batches', {
    method: 'POST',
    body: JSON.stringify({ materials, receiver }),
  })
}

export function claimMaterial(
  batchId: number,
  materialType: string,
  receiver: string,
): Promise<{ ok: boolean; first_claim: boolean; duplicate: boolean; message: string; receipt: Receipt }> {
  return jsonRequest(
    `/api/delivery/batches/${batchId}/claim/${encodeURIComponent(materialType)}`,
    { method: 'POST', body: JSON.stringify({ receiver }) },
  )
}

export function backfillSource(source: string, arrivalTime: string) {
  return jsonRequest('/api/delivery/backfill', {
    method: 'POST',
    body: JSON.stringify({ source, arrival_time: arrivalTime }),
  })
}

export function reprocessBatch(batchId: number): Promise<Batch> {
  return jsonRequest(`/api/delivery/batches/${batchId}/reprocess`, { method: 'POST' })
}

export function fetchBatches(): Promise<{ batches: Batch[] }> {
  return jsonRequest('/api/delivery/batches')
}

export function fetchReceipts(): Promise<{ receipts: Receipt[] }> {
  return jsonRequest('/api/delivery/receipts')
}
