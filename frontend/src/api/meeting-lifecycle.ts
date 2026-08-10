import { apiRequest } from './client'
import type {
  MeetingActionItem,
  MeetingActionItemMutation,
  MeetingLifecycle,
  MeetingListResult,
  MeetingPreparationMutation,
  PostMeetingDraftReviewMutation,
} from './types'

export function listVisibleMeetings(): Promise<MeetingListResult> {
  return apiRequest<MeetingListResult>('/meetings?page=1&size=100')
}

export function getMeetingLifecycle(meetingId: number): Promise<MeetingLifecycle> {
  return apiRequest<MeetingLifecycle>(`/meetings/${meetingId}/lifecycle`)
}

export function saveMeetingPreparation(
  meetingId: number,
  mutation: MeetingPreparationMutation,
): Promise<MeetingLifecycle> {
  return apiRequest<MeetingLifecycle>(`/meetings/${meetingId}/preparation`, {
    method: 'PUT',
    body: JSON.stringify(mutation),
  })
}

export function createPostMeetingDraft(
  meetingId: number,
  transcript: string,
  idempotencyKey: string,
): Promise<MeetingLifecycle> {
  return apiRequest<MeetingLifecycle>(`/meetings/${meetingId}/post-meeting-drafts`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ transcript }),
  })
}

export function reviewPostMeetingDraft(
  meetingId: number,
  draftId: number,
  mutation: PostMeetingDraftReviewMutation,
): Promise<MeetingLifecycle> {
  return apiRequest<MeetingLifecycle>(`/meetings/${meetingId}/post-meeting-drafts/${draftId}/review`, {
    method: 'POST',
    body: JSON.stringify(mutation),
  })
}

export function updateMeetingActionItem(
  meetingId: number,
  actionItemId: number,
  mutation: MeetingActionItemMutation,
): Promise<MeetingActionItem> {
  return apiRequest<MeetingActionItem>(`/meetings/${meetingId}/action-items/${actionItemId}`, {
    method: 'PATCH',
    body: JSON.stringify(mutation),
  })
}
