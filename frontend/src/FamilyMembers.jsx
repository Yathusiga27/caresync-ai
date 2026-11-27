import { useEffect, useRef, useState } from 'react'
import { HeartHandshake, Plus, ShieldCheck, Smile, UserRound, UsersRound } from 'lucide-react'
import { apiRequest, ApiError } from './api.js'
import { BearFamily } from './components/BearIllustration.jsx'
import { Button, Card, Dialog, EmptyState, PageBanner, ResourceContent, StatusIndicator } from './components/ui.jsx'
import { familyPath, isAvailableCaregiver, memberDisplayName, roleLabels } from './familyApi.js'

const memberRoles = [
  ['CHILD', Smile, 'Receives care'],
  ['CAREGIVER', HeartHandshake, 'Accepts care requests'],
  ['GUARDIAN', ShieldCheck, 'Creates care plans'],
]

function AddMemberForm({ onClose, onAdded }) {
  const dialog = useRef(null)
  const request = useRef(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => () => request.current?.abort(), [])

  async function submit(event) {
    event.preventDefault()
    if (request.current) return
    const fields = new FormData(event.currentTarget)
    const name = fields.get('name').trim()
    if (!name) {
      setError('Enter the family member’s name.')
      return
    }

    const controller = new AbortController()
    request.current = controller
    setSaving(true)
    setError('')
    try {
      await apiRequest(`${familyPath}/members`, {
        method: 'POST',
        signal: controller.signal,
        body: { name, role: fields.get('role') },
      })
      if (!controller.signal.aborted) {
        onAdded()
        dialog.current.close()
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof ApiError && typeof failure.detail === 'string'
          ? failure.detail
          : 'Couldn’t add this family member. Check your connection and try again.')
      }
    } finally {
      request.current = null
      if (!controller.signal.aborted) setSaving(false)
    }
  }

  return (
    <Dialog dialogRef={dialog} title="Add family member"
      className="creation-dialog" busy={saving} onClose={onClose}>
      <form onSubmit={submit} className="workspace-form">
        <fieldset disabled={saving} className="creation-group">
          <label className="form-field">
            <span>Name</span>
            <input name="name" autoComplete="off" required />
          </label>
          <fieldset className="member-role-options">
            <legend>Family role</legend>
            {memberRoles.map(([role, Icon, description]) => (
              <label key={role}>
                <input type="radio" name="role" value={role} required />
                <Icon size={22} aria-hidden="true" />
                <strong>{roleLabels[role]}</strong>
                <small>{description}</small>
              </label>
            ))}
          </fieldset>
        </fieldset>
        {error && <p role="alert" className="form-error">{error}</p>}
        <div className="form-actions">
          <Button variant="secondary" onClick={() => dialog.current.close()} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving}>{saving ? 'Adding…' : 'Add member'}</Button>
        </div>
      </form>
    </Dialog>
  )
}

export default function FamilyMembers({ actor, members }) {
  const availabilityRequest = useRef(null)
  const [formOpen, setFormOpen] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [availabilityError, setAvailabilityError] = useState('')
  const [updatingMemberId, setUpdatingMemberId] = useState(null)

  useEffect(() => () => availabilityRequest.current?.abort(), [])

  function onAdded() {
    members.retry()
    setFeedback('Family member added.')
  }

  async function updateAvailability(member, available) {
    if (availabilityRequest.current || actor?.id !== member.id) return
    const controller = new AbortController()
    availabilityRequest.current = controller
    setUpdatingMemberId(member.id)
    setFeedback('')
    setAvailabilityError('')
    try {
      const updated = await apiRequest(`${familyPath}/members/${encodeURIComponent(member.id)}/scheduling-availability`, {
        method: 'PATCH',
        signal: controller.signal,
        body: { actor_id: actor.id, available_for_scheduling: available },
      })
      if (!controller.signal.aborted) {
        members.updateData((people) => people.map((person) => person.id === updated.id ? updated : person))
        const status = updated.available_for_scheduling ? 'available' : 'unavailable'
        setFeedback(`${memberDisplayName(updated)} is ${status} for new schedules.`)
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setAvailabilityError(failure instanceof ApiError && typeof failure.detail === 'string'
          ? failure.detail
          : 'Couldn’t update caregiver availability. Try again.')
      }
    } finally {
      availabilityRequest.current = null
      if (!controller.signal.aborted) setUpdatingMemberId(null)
    }
  }

  return (
    <div className="section-page family-page">
      <PageBanner title="Family members" headingId="family-members-heading" tone="forest" illustration={<BearFamily />}
        description="Caregivers can set their own availability for new schedules.">
        <Button disabled={updatingMemberId !== null}
          onClick={() => { setFeedback(''); setAvailabilityError(''); setFormOpen(true) }}>
          <Plus size={18} aria-hidden="true" /> Add member
        </Button>
      </PageBanner>
      {feedback && <p className="form-success" role="status">{feedback}</p>}
      {availabilityError && <p className="form-error" role="alert">{availabilityError}</p>}
      <Card aria-labelledby="family-members-heading" className="members-card">
        <ResourceContent resource={members} label="family members">
          {(people) => people.length ? (
            <ul className="member-list">
              {[...people].sort((a, b) => memberDisplayName(a).localeCompare(memberDisplayName(b)))
                .map((member) => {
                  const available = isAvailableCaregiver(member)
                  const value = available ? 'available' : 'unavailable'
                  return (
                    <li className="member-row" key={member.id}>
                      <span className="member-icon" data-role={member.role}><UserRound size={18} aria-hidden="true" /></span>
                      <div>
                        <p className="member-name">{memberDisplayName(member)}</p>
                        <p className="overview-muted">{roleLabels[member.role] || member.role}</p>
                      </div>
                      {member.status !== 'ACTIVE' && <StatusIndicator>Inactive</StatusIndicator>}
                      {member.role === 'CAREGIVER' && member.status === 'ACTIVE' && (
                        member.id === actor?.id ? (
                          <select className="member-availability" data-availability={value}
                            aria-label="Your scheduling availability"
                            aria-busy={updatingMemberId === member.id}
                            aria-disabled={updatingMemberId !== null}
                            value={value}
                            onChange={(event) => updateAvailability(member, event.target.value === 'available')}>
                            <option value="available">Available</option>
                            <option value="unavailable">Unavailable</option>
                          </select>
                        ) : (
                          <StatusIndicator tone={available ? 'teal' : 'amber'}>
                            {available ? 'Available' : 'Unavailable'}
                          </StatusIndicator>
                        )
                      )}
                    </li>
                  )
                })}
            </ul>
          ) : (
            <EmptyState icon={UsersRound} title="No family members"
              description="Add a family member to start planning supervision." />
          )}
        </ResourceContent>
      </Card>
      {formOpen && <AddMemberForm onClose={() => setFormOpen(false)} onAdded={onAdded} />}
    </div>
  )
}
