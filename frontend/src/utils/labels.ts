const MEETING_TYPE_LABELS: Record<string, string> = {
  GENERAL: '常规会议',
  ARCHITECTURE_REVIEW: '架构评审',
  PROJECT_REVIEW: '项目评审',
  PRODUCT_REVIEW: '产品评审',
  CUSTOMER_MEETING: '客户会议',
  SALES_REVIEW: '销售复盘',
  TEAM_SYNC: '团队同步',
  TRAINING: '培训会议',
  INTERVIEW: '招聘面试',
  SECURITY_REVIEW: '安全评审',
  CONTRACT_REVIEW: '合同评审',
  QUARTERLY_REVIEW: '季度复盘',
  RETROSPECTIVE: '项目复盘',
  CONCURRENCY_TEST: '内部验证会议',
}

const ROOM_TYPE_LABELS: Record<string, string> = {
  STANDARD: '标准会议室',
  HUDDLE: '小型讨论室',
  VIDEO: '远程协作室',
  TRAINING: '培训室',
  BOARDROOM: '董事会议室',
  VIP: '贵宾会议室',
  PHONE_BOOTH: '电话间',
  AUDITORIUM: '多功能厅',
  WORKSHOP: '共创工作坊',
  INTERVIEW: '面试室',
  CLIENT: '客户接待室',
  FOCUS: '专注讨论室',
}

const AGENT_INTENT_LABELS: Record<string, string> = {
  CREATE_MEETING: '创建会议',
  FIND_COMMON_TIME: '协调共同时间',
  RECOMMEND_ROOM: '推荐会议室',
  MODIFY_MEETING: '调整会议',
  CANCEL_MEETING: '取消会议',
  QUERY_POLICY: '查询会议制度',
  UPDATE_PREFERENCE: '更新调度偏好',
}

export const meetingTypeOptions = Object.entries(MEETING_TYPE_LABELS)
  .filter(([value]) => value !== 'CONCURRENCY_TEST')
  .map(([value, label]) => ({ value, label }))

export const roomTypeOptions = Object.entries(ROOM_TYPE_LABELS)
  .map(([value, label]) => ({ value, label }))

export function meetingTypeLabel(value: string): string {
  return MEETING_TYPE_LABELS[value] ?? '其他会议'
}

export function roomTypeLabel(value: string): string {
  return ROOM_TYPE_LABELS[value] ?? '其他会议空间'
}

export function agentIntentLabel(value: string | null | undefined): string {
  return value === null || value === undefined || value.length === 0
    ? '智能编排任务'
    : AGENT_INTENT_LABELS[value] ?? '其他智能编排任务'
}

export function isTechnicalDemoMeeting(title: string, meetingType: string): boolean {
  if (meetingType === 'CONCURRENCY_TEST') return true
  return /(?:Day\s*[1-7]|smoke|验收|并发|幂等|slot persistence|Redis unavailable|MQ conflict|Exception Replan Visual QA|\?{4,})/iu.test(title)
}

export function isTechnicalDemoNotification(title: string, content: string): boolean {
  return /(?:Day\s*[1-7]|smoke|验收|并发|幂等|concurrency|slot persistence|Redis unavailable|HOT MQ|MQ conflict|Visual QA|Meeting (?:confirmed|changed|cancelled)|meetingId=\d+|\?{4,})/iu.test(`${title} ${content}`)
}
