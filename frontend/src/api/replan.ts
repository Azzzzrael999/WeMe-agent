import { apiRequest } from './client'
import type {
  ReplanAlternatives,
  ReplanCase,
  ReplanCaseListResult,
  ReplanCaseStatus,
  ReplanResolveMutation,
} from './types'

export interface ReplanCaseQuery {
  status?: ReplanCaseStatus
  page: number
  size: number
}

export function listReplanCases(query: ReplanCaseQuery): Promise<ReplanCaseListResult> {
  const search = new URLSearchParams({ page: String(query.page), size: String(query.size) })
  if (query.status !== undefined) search.set('status', query.status)
  return apiRequest<ReplanCaseListResult>(`/replan-cases?${search.toString()}`)
}

export function getReplanCase(caseId: number): Promise<ReplanCase> {
  return apiRequest<ReplanCase>(`/replan-cases/${caseId}`)
}

export function getReplanAlternatives(caseId: number, limit = 3): Promise<ReplanAlternatives> {
  return apiRequest<ReplanAlternatives>(`/replan-cases/${caseId}/alternatives?limit=${limit}`)
}

export function resolveReplanCase(caseId: number, mutation: ReplanResolveMutation): Promise<ReplanCase> {
  return apiRequest<ReplanCase>(`/replan-cases/${caseId}/resolve`, {
    method: 'POST',
    body: JSON.stringify(mutation),
  })
}
