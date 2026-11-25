<template>
  <section class="page" data-module="delivery">
    <header class="page-head">
      <div>
        <h2>监控交付</h2>
        <p class="page-desc">
          按到账数据的实际时效计算导出批次交付资格：两张源表都及时才能交付混合导出，缺任一来源不能领取对应材料。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="refresh" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <h3>源表时效（实际到数时间）</h3>
    <table class="data-table" data-testid="source-table">
      <thead>
        <tr>
          <th>数据来源</th><th>期望时效</th><th>实际到数时间</th>
          <th>实际延迟（小时）</th><th>时效状态</th><th>交付资格</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="src in sourceRows" :key="src.source" :data-testid="`source-${src.source}`">
          <td>{{ src.source }}</td>
          <td>{{ src.expected_text ?? '—' }}</td>
          <td>{{ src.actual_arrival_time ?? '—' }}</td>
          <td>{{ src.actual_delay_hours ?? '—' }}</td>
          <td>{{ src.status }}</td>
          <td :data-testid="`eligible-${src.source}`">{{ src.eligible ? '可交付' : '不可交付' }}</td>
        </tr>
      </tbody>
    </table>

    <h3>提交导出批次</h3>
    <form class="filter-bar" @submit.prevent="onSubmit">
      <label v-for="item in materialOptions" :key="item.material_type" class="filter-item">
        <input
          type="checkbox"
          :value="item.material_type"
          v-model="selectedMaterials"
          :data-testid="`pick-${item.material_type}`"
        />
        <span>{{ item.material_name }}（{{ item.copies }}份，依赖：{{ item.required_sources.join('、') }}）</span>
      </label>
      <label class="filter-item">
        <span>接收方</span>
        <input v-model="receiver" placeholder="如：退款组" data-testid="receiver" required />
      </label>
      <button class="btn primary" type="submit" data-testid="submit-batch" :disabled="submitting">
        {{ submitting ? '提交中…' : '提交批次' }}
      </button>
    </form>

    <h3>交付批次</h3>
    <table class="data-table" data-testid="batch-table">
      <thead>
        <tr>
          <th>批次号</th><th>接收方</th><th>提交时间</th><th>批次状态</th>
          <th>材料 / 份数 / 资格</th><th>领取凭据</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="batch in batches" :key="batch.batch_id" :data-testid="`batch-${batch.batch_id}`">
          <td>{{ batch.batch_id }}</td>
          <td>{{ batch.receiver }}</td>
          <td>{{ batch.submitted_at }}</td>
          <td>{{ batch.status }}（重处理 {{ batch.reprocess_count }} 次）</td>
          <td>
            <div v-for="m in batch.materials" :key="m.material_type" class="material-line">
              <strong>{{ m.material_name }}</strong> × {{ m.copies }}：
              <span :data-testid="`status-${batch.batch_id}-${m.material_type}`">
                {{ m.eligible ? (m.delivered ? '已签收' : '可领取') : '不可领取' }}
              </span>
              <ul v-if="m.blocked_reasons.length" class="blocked-list">
                <li v-for="reason in m.blocked_reasons" :key="reason.source">
                  {{ reason.source }}（{{ reason.status }}）：{{ reason.message }}
                </li>
              </ul>
            </div>
            <ul v-if="batch.notices.length" class="blocked-list">
              <li v-for="(notice, idx) in batch.notices" :key="idx">{{ notice }}</li>
            </ul>
          </td>
          <td>
            <div v-for="m in batch.materials" :key="m.material_type" class="material-line">
              <span v-if="m.claim_token">{{ m.material_name }}：{{ m.claim_token }}</span>
              <span v-else class="muted">{{ m.material_name }}：—</span>
            </div>
          </td>
          <td class="row-actions">
            <button
              v-for="m in batch.materials.filter((row) => row.eligible)"
              :key="m.material_type"
              class="link"
              type="button"
              :data-testid="`claim-${batch.batch_id}-${m.material_type}`"
              @click="onClaim(batch.batch_id, m.material_type)"
            >
              领取{{ m.material_name }}
            </button>
            <button
              class="link"
              type="button"
              :data-testid="`reprocess-${batch.batch_id}`"
              @click="onReprocess(batch.batch_id)"
            >
              原批再次处理
            </button>
          </td>
        </tr>
        <tr v-if="!batches.length">
          <td colspan="7" class="empty-state">暂无交付批次</td>
        </tr>
      </tbody>
    </table>

    <h3>到账数据补到</h3>
    <form class="filter-bar" @submit.prevent="onBackfill">
      <label class="filter-item">
        <span>来源</span>
        <select v-model="backfillSourceName" data-testid="backfill-source">
          <option v-for="src in sourceRows" :key="src.source" :value="src.source">{{ src.source }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>实际到数时间</span>
        <input v-model="backfillTime" data-testid="backfill-time" required />
      </label>
      <button class="btn primary" type="submit" data-testid="backfill-submit">登记补到并重判时效</button>
    </form>

    <h3>领取回执（接收方可查份数与真实来源数据）</h3>
    <table class="data-table" data-testid="receipt-table">
      <thead>
        <tr>
          <th>回执号</th><th>批次</th><th>材料</th><th>份数</th><th>采用的真实来源数据</th>
          <th>凭据</th><th>签收</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="receipt in receipts" :key="receipt.receipt_id" :data-testid="`receipt-${receipt.receipt_id}`">
          <td>{{ receipt.receipt_id }}</td>
          <td>{{ receipt.batch_id }}</td>
          <td>{{ receipt.material_name }}</td>
          <td>{{ receipt.copies }}</td>
          <td>
            <div v-for="s in receipt.source_data" :key="s.source" class="material-line">
              {{ s.source }}：期望 {{ s.expected_text }}，实际到数 {{ s.actual_arrival_time }}（{{ s.status }}）
            </div>
          </td>
          <td>{{ receipt.claim_token }}</td>
          <td>{{ receipt.claimed ? `已签收 ${receipt.claimed_at ?? ''}` : '待领取' }}</td>
        </tr>
        <tr v-if="!receipts.length">
          <td colspan="7" class="empty-state">暂无回执</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  claimMaterial,
  fetchBatches,
  fetchMaterials,
  fetchReceipts,
  fetchSourceStatuses,
  reprocessBatch,
  submitBatch,
  backfillSource,
} from '@/api/delivery'
import type { Batch, MaterialCatalog, Receipt, SourceStatus } from '@/api/delivery'

const sources = ref<Record<string, SourceStatus>>({})
const materialOptions = ref<MaterialCatalog[]>([])
const batches = ref<Batch[]>([])
const receipts = ref<Receipt[]>([])
const selectedMaterials = ref<string[]>(['到账明细', '退款明细', '混合明细'])
const receiver = ref('退款组')
const backfillSourceName = ref('到账小时表')
const backfillTime = ref('')
const submitting = ref(false)
const errorMessage = ref('')

const sourceRows = computed(() => Object.values(sources.value))

async function reload() {
  errorMessage.value = ''
  try {
    const [sourceResp, materialResp, batchResp, receiptResp] = await Promise.all([
      fetchSourceStatuses(),
      fetchMaterials(),
      fetchBatches(),
      fetchReceipts(),
    ])
    sources.value = sourceResp.sources
    materialOptions.value = materialResp.materials
    batches.value = batchResp.batches
    receipts.value = receiptResp.receipts
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '监控交付数据读取失败'
  }
}

async function onSubmit() {
  submitting.value = true
  errorMessage.value = ''
  try {
    await submitBatch(selectedMaterials.value, receiver.value)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次提交失败'
  } finally {
    submitting.value = false
  }
}

async function onClaim(batchId: number, materialType: string) {
  errorMessage.value = ''
  try {
    const result = await claimMaterial(batchId, materialType, receiver.value)
    window.alert(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领取失败'
  }
}

async function onReprocess(batchId: number) {
  errorMessage.value = ''
  try {
    await reprocessBatch(batchId)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '原批重处理失败'
  }
}

async function onBackfill() {
  errorMessage.value = ''
  try {
    await backfillSource(backfillSourceName.value, backfillTime.value)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补到登记失败'
  }
}

onMounted(() => {
  const now = new Date(Date.now() - 20 * 60 * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  backfillTime.value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
  void reload()
})
</script>

<style scoped>
.material-line {
  margin: 4px 0;
}
.blocked-list {
  margin: 4px 0 0;
  padding-left: 18px;
  color: #b34700;
}
.muted {
  color: #888;
}
.filter-bar select {
  padding: 6px 8px;
}
</style>
