export interface ApiSuccess<T> {
  data: T
  traceId: string
  timestamp: string
}

export interface ApiErrorDetail {
  field?: string
  reason: string
}

export interface ApiFailure {
  code: string
  message: string
  details: ApiErrorDetail[]
  traceId: string
}

export interface CurrentUser {
  id: number
  username: string
  displayName: string
  email: string
  departmentId: number
  departmentName: string
  roles: string[]
}

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResult {
  accessToken: string
  tokenType: 'Bearer'
  expiresIn: number
  user: CurrentUser
}

export interface RoomFeature {
  code: string
  name: string
}

export interface MeetingRoom {
  id: number
  code: string
  name: string
  building: string
  floor: string
  capacity: number
  roomType: string
  isHot: boolean
  status: string
  features: RoomFeature[]
}

export interface RoomListResult {
  items: MeetingRoom[]
  total: number
}
