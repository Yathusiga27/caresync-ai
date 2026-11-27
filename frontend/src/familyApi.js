import { apiRequest } from './api.js'

export const familyId = 'family-demo'
export const familyPath = `/families/${familyId}`
export const scheduleRefreshMs = 10_000
const memberLabels = {
  'guardian-demo': ['Demo Guardian', 'Maya Perera'],
  'caregiver-alt-demo': ['Demo Backup Caregiver', 'Nilan Perera'],
  'caregiver-demo': ['Demo Caregiver', 'Soma Perera'],
  'child-demo': ['Demo Child', 'Tara Perera'],
}

export const roleLabels = { GUARDIAN: 'Guardian', CAREGIVER: 'Caregiver', CHILD: 'Child' }

export function memberDisplayName({ id, name }) {
  const label = memberLabels[id]
  return label && label[0] === name ? label[1] : name
}

export function isAvailableCaregiver(member) {
  return member.role === 'CAREGIVER' && member.status === 'ACTIVE'
    && member.available_for_scheduling !== false
}

export async function loadList(path, signal) {
  const data = await apiRequest(path, { signal })
  if (!Array.isArray(data)) throw new Error('Expected a list from the API.')
  return data
}

export function loadFamilyMembers(signal) {
  return loadList(`${familyPath}/members`, signal)
}

export function loadFamilyContracts(signal) {
  return loadList(`${familyPath}/contracts`, signal)
}

export function loadScheduleRequests(actorId, signal) {
  return loadList(`${familyPath}/schedule-requests?${new URLSearchParams({ actor_id: actorId })}`, signal)
}

export function updateScheduleContract(contractId, action, body, signal) {
  return apiRequest(`/supervision/contracts/${encodeURIComponent(contractId)}/${action}`, {
    method: 'POST', signal, body,
  })
}

export function respondToSchedule(contract, actorId, decision, reason, signal) {
  return updateScheduleContract(contract.id, 'schedule-decisions', {
    actor_id: actorId, decision, expected_version: contract.version,
    ...(reason && { reason }),
  }, signal)
}
