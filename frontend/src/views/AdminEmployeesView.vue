<template>
  <AppShell title="员工管理" description="管理账户、组织归属和访问状态。" eyebrow="管理 / 员工">
    <template #actions>
      <button class="ui-button ui-button--default" type="button" @click="openCreate">
        <UserPlus :size="16" aria-hidden="true" />新增员工
      </button>
    </template>

    <section class="management-filters content-panel" aria-label="员工筛选">
      <label class="management-filter--search"><span>搜索员工</span><input v-model.trim="filters.keyword" type="search" placeholder="用户名、姓名或邮箱" @keyup.enter="applyFilters" /></label>
      <label><span>部门</span><select v-model="filters.departmentId"><option value="">全部部门</option><option v-for="department in departments" :key="department.id" :value="String(department.id)">{{ department.name }}</option></select></label>
      <label><span>角色</span><select v-model="filters.role"><option value="">全部角色</option><option value="EMPLOYEE">员工</option><option value="ADMIN">管理员</option></select></label>
      <label><span>状态</span><select v-model="filters.status"><option value="">全部状态</option><option value="ACTIVE">已启用</option><option value="DISABLED">已停用</option></select></label>
      <button class="ui-button ui-button--outline" type="button" @click="resetFilters"><RotateCcw :size="15" aria-hidden="true" />重置</button>
      <button class="ui-button ui-button--default" type="button" @click="applyFilters"><Search :size="15" aria-hidden="true" />查询</button>
    </section>

    <ErrorState v-if="listError" :message="listError" retryable @retry="loadEmployees" />
    <div v-else-if="loading" class="feedback-state" aria-live="polite"><span class="spinner" aria-hidden="true" />正在加载员工…</div>
    <EmptyState v-else-if="employees.length === 0" title="没有匹配的员工" description="调整筛选条件，或新增一名员工账户。" icon="search" />
    <section v-else class="management-list content-panel" aria-label="员工列表">
      <div class="management-table-wrap">
        <table class="management-table">
          <thead><tr><th>员工</th><th>部门</th><th>角色</th><th>状态</th><th>最近更新</th><th><span class="sr-only">操作</span></th></tr></thead>
          <tbody>
            <tr v-for="employee in employees" :key="employee.id">
              <td><div class="employee-identity"><span class="employee-avatar" aria-hidden="true">{{ employee.displayName.slice(0, 1) }}</span><div><strong>{{ employee.displayName }}</strong><small>@{{ employee.username }} · {{ employee.email }}</small></div></div></td>
              <td>{{ employee.departmentName ?? '未分配' }}</td>
              <td><span class="badge">{{ employee.role === 'ADMIN' ? '管理员' : '员工' }}</span></td>
              <td><StatusBadge :status="employee.status" /></td>
              <td>{{ formatDateTime(employee.updatedAt) }}</td>
              <td><div class="management-actions"><button class="text-button" type="button" @click="openEdit(employee)">编辑</button><button class="text-button" type="button" @click="openPassword(employee)">重置密码</button><button class="text-button" :class="{ 'text-button--danger': employee.status === 'ACTIVE' }" type="button" :disabled="isSelf(employee) && employee.status === 'ACTIVE'" @click="requestStatus(employee)">{{ employee.status === 'ACTIVE' ? '停用' : '启用' }}</button></div></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="employee-mobile-list">
        <article v-for="employee in employees" :key="employee.id" class="employee-mobile-card">
          <header><div class="employee-identity"><span class="employee-avatar" aria-hidden="true">{{ employee.displayName.slice(0, 1) }}</span><div><strong>{{ employee.displayName }}</strong><small>@{{ employee.username }}</small></div></div><StatusBadge :status="employee.status" /></header>
          <dl><div><dt>邮箱</dt><dd>{{ employee.email }}</dd></div><div><dt>部门</dt><dd>{{ employee.departmentName ?? '未分配' }}</dd></div><div><dt>角色</dt><dd>{{ employee.role === 'ADMIN' ? '管理员' : '员工' }}</dd></div></dl>
          <footer class="management-actions"><button class="text-button" type="button" @click="openEdit(employee)">编辑</button><button class="text-button" type="button" @click="openPassword(employee)">重置密码</button><button class="text-button" :class="{ 'text-button--danger': employee.status === 'ACTIVE' }" type="button" :disabled="isSelf(employee) && employee.status === 'ACTIVE'" @click="requestStatus(employee)">{{ employee.status === 'ACTIVE' ? '停用' : '启用' }}</button></footer>
        </article>
      </div>

      <footer class="pagination-bar">
        <span>共 {{ total }} 名员工 · 第 {{ page }} / {{ totalPages }} 页</span>
        <div><button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="page <= 1" @click="changePage(page - 1)"><ChevronLeft :size="15" aria-hidden="true" />上一页</button><button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="page >= totalPages" @click="changePage(page + 1)">下一页<ChevronRight :size="15" aria-hidden="true" /></button></div>
      </footer>
    </section>

    <Teleport to="body">
      <div v-if="editorOpen" class="drawer-layer">
        <button class="drawer-overlay" type="button" aria-label="关闭员工编辑" @click="closeEditor" />
        <aside class="trace-drawer product-sheet" role="dialog" aria-modal="true" aria-labelledby="employee-editor-title">
          <header class="product-sheet__header"><div><p>{{ editingEmployee ? `员工 #${editingEmployee.id}` : '新账户' }}</p><h2 id="employee-editor-title">{{ editingEmployee ? '编辑员工' : '新增员工' }}</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="closeEditor"><X :size="18" aria-hidden="true" /></button></header>
          <p class="product-sheet__notice">用户名创建后不可修改。</p>
          <form class="form-grid product-sheet__form" @submit.prevent="saveEmployee">
            <label><span>用户名</span><input v-model.trim="employeeForm.username" :disabled="editingEmployee !== null || submitting" minlength="3" maxlength="64" pattern="[A-Za-z0-9._-]{3,64}" autocomplete="off" required /></label>
            <label v-if="editingEmployee === null"><span>初始密码</span><input v-model="employeeForm.initialPassword" :disabled="submitting" type="password" minlength="8" maxlength="72" autocomplete="new-password" required /></label>
            <label><span>显示名</span><input v-model.trim="employeeForm.displayName" :disabled="submitting" maxlength="64" required /></label>
            <label><span>邮箱</span><input v-model.trim="employeeForm.email" :disabled="submitting" type="email" maxlength="128" required /></label>
            <label><span>部门</span><select v-model="employeeForm.departmentId" :disabled="submitting"><option value="">未分配</option><option v-for="department in departments" :key="department.id" :value="String(department.id)">{{ department.name }}</option></select></label>
            <label><span>角色</span><select v-model="employeeForm.role" :disabled="submitting || isEditingSelf"><option value="EMPLOYEE">员工</option><option value="ADMIN">管理员</option></select></label>
            <label v-if="editingEmployee === null"><span>初始状态</span><select v-model="employeeForm.status" :disabled="submitting"><option value="ACTIVE">启用</option><option value="DISABLED">停用</option></select></label>
            <p v-if="formError" class="error-message form-span-2" role="alert">{{ formError }}</p>
            <footer class="product-sheet__actions form-span-2"><button class="ui-button ui-button--outline" type="button" :disabled="submitting" @click="closeEditor">返回</button><button class="ui-button ui-button--default" type="submit" :disabled="submitting">{{ submitting ? '正在保存…' : '保存员工' }}</button></footer>
          </form>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="statusTarget" class="dialog-layer">
        <button class="drawer-overlay" type="button" aria-label="关闭状态确认" @click="statusTarget = null" />
        <section class="ui-dialog ui-dialog--sm" role="alertdialog" aria-modal="true" aria-labelledby="employee-status-title"><h2 id="employee-status-title">{{ statusTarget.status === 'ACTIVE' ? '停用' : '启用' }}“{{ statusTarget.displayName }}”？</h2><p>{{ statusTarget.status === 'ACTIVE' ? '该员工现有登录凭证将在下次请求时失效，历史会议与消息仍会保留。' : '该员工将重新获得登录和业务访问权限。' }}</p><p v-if="statusError" class="error-message" role="alert">{{ statusError }}</p><footer><button class="ui-button ui-button--outline" type="button" :disabled="submitting" @click="statusTarget = null">返回</button><button class="ui-button" :class="statusTarget.status === 'ACTIVE' ? 'ui-button--destructive' : 'ui-button--default'" type="button" :disabled="submitting" @click="changeStatus">确认{{ statusTarget.status === 'ACTIVE' ? '停用' : '启用' }}</button></footer></section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="passwordTarget" class="dialog-layer">
        <button class="drawer-overlay" type="button" aria-label="关闭密码重置" @click="closePassword" />
        <form class="ui-dialog ui-dialog--sm" role="dialog" aria-modal="true" aria-labelledby="employee-password-title" @submit.prevent="resetPassword"><h2 id="employee-password-title">重置“{{ passwordTarget.displayName }}”的密码</h2><p>新密码不会在页面保存或回显。</p><label><span>新密码</span><input v-model="newPassword" type="password" minlength="8" maxlength="72" autocomplete="new-password" :disabled="submitting" required /></label><p v-if="passwordError" class="error-message" role="alert">{{ passwordError }}</p><footer><button class="ui-button ui-button--outline" type="button" :disabled="submitting" @click="closePassword">返回</button><button class="ui-button ui-button--default" type="submit" :disabled="submitting">{{ submitting ? '正在重置…' : '确认重置' }}</button></footer></form>
      </div>
    </Teleport>
  </AppShell>
</template>

<script setup lang="ts">
import { ChevronLeft, ChevronRight, RotateCcw, Search, UserPlus, X } from '@lucide/vue'
import { computed, onMounted, reactive, ref } from 'vue'

import { ApiError, apiRequest } from '@/api/client'
import type { DepartmentListResult, DepartmentOption, Employee, EmployeeCreateMutation, EmployeeListResult, EmployeePasswordMutation, EmployeeRole, EmployeeStatus, EmployeeStatusMutation, EmployeeUpdateMutation } from '@/api/types'
import { authStore } from '@/auth/store'
import AppShell from '@/components/AppShell.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useModalFocus } from '@/composables/useModalFocus'
import { formatDateTime } from '@/utils/format'

const PAGE_SIZE = 20
const employees = ref<Employee[]>([])
const departments = ref<DepartmentOption[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(true)
const listError = ref('')
const submitting = ref(false)
const formError = ref('')
const statusError = ref('')
const passwordError = ref('')
const editingEmployee = ref<Employee | null>(null)
const editorOpen = ref(false)
const statusTarget = ref<Employee | null>(null)
const passwordTarget = ref<Employee | null>(null)
const newPassword = ref('')
const filters = reactive({ keyword: '', departmentId: '', role: '', status: '' })
const employeeForm = reactive({ username: '', initialPassword: '', displayName: '', email: '', departmentId: '', role: 'EMPLOYEE' as EmployeeRole, status: 'ACTIVE' as EmployeeStatus })
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const isEditingSelf = computed(() => editingEmployee.value?.id === authStore.state.user?.id)
const modalOpen = computed(() => editorOpen.value || statusTarget.value !== null || passwordTarget.value !== null)
useModalFocus(modalOpen, closeAllOverlays)

function asMessage(error: unknown, fallback: string): string { return error instanceof ApiError ? error.message : fallback }
function isSelf(employee: Employee): boolean { return employee.id === authStore.state.user?.id }
function departmentId(value: string): number | null { return value === '' ? null : Number(value) }
function blankForm(): void { Object.assign(employeeForm, { username: '', initialPassword: '', displayName: '', email: '', departmentId: '', role: 'EMPLOYEE', status: 'ACTIVE' }) }

async function loadDepartments(): Promise<void> {
  try { departments.value = (await apiRequest<DepartmentListResult>('/admin/departments')).items }
  catch (error) { listError.value = asMessage(error, '部门列表加载失败。') }
}
async function loadEmployees(): Promise<void> {
  loading.value = true; listError.value = ''
  const query = new URLSearchParams({ page: String(page.value), size: String(PAGE_SIZE) })
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.departmentId) query.set('departmentId', filters.departmentId)
  if (filters.role) query.set('role', filters.role)
  if (filters.status) query.set('status', filters.status)
  try { const result = await apiRequest<EmployeeListResult>(`/admin/employees?${query.toString()}`); employees.value = result.items; total.value = result.total }
  catch (error) { listError.value = asMessage(error, '员工列表加载失败，请稍后重试。') }
  finally { loading.value = false }
}
function applyFilters(): void { page.value = 1; void loadEmployees() }
function resetFilters(): void { Object.assign(filters, { keyword: '', departmentId: '', role: '', status: '' }); applyFilters() }
function changePage(value: number): void { page.value = value; void loadEmployees() }
function openCreate(): void { editingEmployee.value = null; blankForm(); formError.value = ''; editorOpen.value = true }
function openEdit(employee: Employee): void { editingEmployee.value = employee; Object.assign(employeeForm, { username: employee.username, initialPassword: '', displayName: employee.displayName, email: employee.email, departmentId: employee.departmentId === null ? '' : String(employee.departmentId), role: employee.role, status: employee.status }); formError.value = ''; editorOpen.value = true }
function closeEditor(): void { employeeForm.initialPassword = ''; editorOpen.value = false; editingEmployee.value = null }
function requestStatus(employee: Employee): void { statusError.value = ''; statusTarget.value = employee }
function openPassword(employee: Employee): void { newPassword.value = ''; passwordError.value = ''; passwordTarget.value = employee }
function closePassword(): void { newPassword.value = ''; passwordTarget.value = null }
function closeAllOverlays(): void { closeEditor(); statusTarget.value = null; closePassword() }

async function saveEmployee(): Promise<void> {
  if (submitting.value) return
  submitting.value = true; formError.value = ''
  try {
    if (editingEmployee.value === null) {
      const payload: EmployeeCreateMutation = { username: employeeForm.username.toLocaleLowerCase('en-US'), initialPassword: employeeForm.initialPassword, displayName: employeeForm.displayName, email: employeeForm.email, departmentId: departmentId(employeeForm.departmentId), role: employeeForm.role, status: employeeForm.status }
      await apiRequest<Employee>('/admin/employees', { method: 'POST', body: JSON.stringify(payload) })
    } else {
      const payload: EmployeeUpdateMutation = { displayName: employeeForm.displayName, email: employeeForm.email, departmentId: departmentId(employeeForm.departmentId), role: employeeForm.role, expectedVersion: editingEmployee.value.version }
      await apiRequest<Employee>(`/admin/employees/${editingEmployee.value.id}`, { method: 'PUT', body: JSON.stringify(payload) })
    }
    closeEditor(); await loadEmployees()
  } catch (error) { formError.value = asMessage(error, '员工保存失败。') }
  finally { employeeForm.initialPassword = ''; submitting.value = false }
}
async function changeStatus(): Promise<void> {
  const employee = statusTarget.value
  if (employee === null || submitting.value) return
  submitting.value = true; statusError.value = ''
  const payload: EmployeeStatusMutation = { status: employee.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE', expectedVersion: employee.version }
  try { await apiRequest<Employee>(`/admin/employees/${employee.id}/status`, { method: 'PATCH', body: JSON.stringify(payload) }); statusTarget.value = null; await loadEmployees() }
  catch (error) { statusError.value = asMessage(error, '员工状态更新失败。') }
  finally { submitting.value = false }
}
async function resetPassword(): Promise<void> {
  const employee = passwordTarget.value
  if (employee === null || submitting.value) return
  submitting.value = true; passwordError.value = ''
  const payload: EmployeePasswordMutation = { newPassword: newPassword.value, expectedVersion: employee.version }
  try { await apiRequest<Employee>(`/admin/employees/${employee.id}/password`, { method: 'POST', body: JSON.stringify(payload) }); closePassword(); await loadEmployees() }
  catch (error) { passwordError.value = asMessage(error, '密码重置失败。') }
  finally { newPassword.value = ''; submitting.value = false }
}

onMounted(() => { void Promise.all([loadDepartments(), loadEmployees()]) })
</script>
