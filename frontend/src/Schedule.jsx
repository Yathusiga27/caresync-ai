import { useEffect, useId, useRef, useState } from 'react'
import { CalendarRange, CheckCircle2, ChevronLeft, ChevronRight, ClipboardCheck, Clock, MapPin, Plus, UserRound, UsersRound } from 'lucide-react'
import { apiRequest, ApiError } from './api.js'
import { BannerIllustration, Button, Card, Dialog, EmptyState, PageBanner, ResourceContent, StatusIndicator } from './components/ui.jsx'
import { familyId, isAvailableCaregiver, memberDisplayName, respondToSchedule, updateScheduleContract } from './familyApi.js'
import './schedule.css'
import { BearActivity } from './components/BearIllustration.jsx'

const dayFormat = new Intl.DateTimeFormat(undefined, {
  weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
})
const weekFormat = new Intl.DateTimeFormat(undefined, {
  month: 'short', day: 'numeric', year: 'numeric',
})
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
const dateTimeFormat = new Intl.DateTimeFormat(undefined, {
  month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
})
const detailDateTimeFormat = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'long', timeStyle: 'short',
})

const scheduleStatuses = {
  DRAFT: ['Draft', 'neutral'],
  PENDING_APPROVAL: ['Awaiting caregiver response', 'amber'],
  DECLINED: ['Caregiver declined', 'red'],
  CONFIRMED: ['Awaiting handover', 'teal'],
  ACTIVE: ['Handover confirmed', 'teal'],
  CONFLICTED: ['Conflict', 'amber'],
  ESCALATED: ['Escalated', 'red'],
  CANCELLED: ['Cancelled', 'neutral'],
  EXPIRED: ['Expired', 'neutral'],
  COMPLETED: ['Completed', 'neutral'],
}

const suggestedActivities = [
  'School drop-off',
  'School pickup',
  'After-school care',
  'Activity drop-off',
  'Activity pickup',
  'Evening supervision',
]

const cancellableStatuses = new Set(['DRAFT', 'PENDING_APPROVAL', 'CONFIRMED', 'DECLINED'])

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

function daysFrom(date, days) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days)
}

function startOfWeek(date) {
  return daysFrom(date, -((date.getDay() + 6) % 7))
}

function inputTime(date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function scheduleStatus({ status, end_time: endTime }) {
  if (status === 'PENDING_APPROVAL' && Date.now() >= new Date(endTime).getTime()) {
    return ['Request expired', 'neutral']
  }
  return scheduleStatuses[status] || [status, 'neutral']
}

function contractsInWindow(contracts, start, end) {
  return contracts.filter((contract) => new Date(contract.start_time) < end && new Date(contract.end_time) > start)
}

function ScheduleEntries({ actor, contracts, members, view = 'week', emptyTitle, emptyDescription, onCancel, onUpdated }) {
  const [selectedId, setSelectedId] = useState(null)

  if (!contracts.length) {
    return (
      <EmptyState
        icon={CalendarRange}
        tone="mint"
        title={emptyTitle ?? (view === 'today' ? 'No plans for today' : `No plans this ${view}`)}
        description={emptyDescription ?? 'Scheduled supervision will appear here.'}
        heading="h3"
      />
    )
  }

  const names = new Map(members.map((member) => [member.id, memberDisplayName(member)]))
  const selected = contracts.find(({ id }) => id === selectedId)
  return (
    <>
      <ul className="schedule-entries">
        {contracts.map((contract) => {
          const starts = new Date(contract.start_time)
          const ends = new Date(contract.end_time)
          const crossesDate = startOfDay(starts).getTime() !== startOfDay(ends).getTime()
          const startFormat = view === 'week' || crossesDate ? dateTimeFormat : timeFormat
          const endFormat = crossesDate ? dateTimeFormat : timeFormat
          const [status, tone] = scheduleStatus(contract)
          const declineReason = actor?.role === 'GUARDIAN' && actor.id === contract.guardian_id
            && contract.decline_reason
          return (
            <li key={contract.id} className="schedule-entry">
              <div className="schedule-entry-main">
                <h3>{contract.activity}</h3>
                <div className="schedule-entry-actions">
                  <StatusIndicator tone={tone}>{status}</StatusIndicator>
                  <Button variant="secondary" className="schedule-entry-button"
                    aria-label={`View details for ${contract.activity}`}
                    onClick={() => setSelectedId(contract.id)}>
                    View details
                  </Button>
                  {onCancel && actor?.role === 'GUARDIAN'
                    && (!contract.guardian_id || contract.guardian_id === actor.id)
                    && cancellableStatuses.has(contract.status) && (
                    <Button variant="secondary" className="schedule-entry-button"
                      aria-label={`Cancel schedule for ${contract.activity}`}
                      onClick={() => onCancel(contract)}>
                      Cancel schedule
                    </Button>
                  )}
                </div>
              </div>
              <div className="schedule-details">
                <span>
                  <Clock size={15} aria-hidden="true" />
                  <span>
                    <time dateTime={contract.start_time}>{startFormat.format(starts)}</time>
                    {' – '}
                    <time dateTime={contract.end_time}>{endFormat.format(ends)}</time>
                  </span>
                </span>
                <span>
                  <UserRound size={15} aria-hidden="true" />
                  {names.get(contract.caregiver_id) || 'Caregiver'} with {names.get(contract.child_id) || 'child'}
                </span>
                <span><MapPin size={15} aria-hidden="true" />{contract.expected_location}</span>
              </div>
              {declineReason && <p className="schedule-entry-decline"><strong>Decline reason</strong> {declineReason}</p>}
            </li>
          )
        })}
      </ul>
      {selected && (
        <ScheduleDetails key={`${actor?.id}:${selected.id}`} actor={actor} contract={selected}
          members={members} names={names}
          onClose={() => setSelectedId((id) => id === selected.id ? null : id)} onUpdated={onUpdated} />
      )}
    </>
  )
}

function decisionLabel(value) {
  return value.charAt(0) + value.slice(1).toLowerCase()
}

export function DeclineScheduleForm({ busy, error, onBack, onDecline }) {
  const helpId = useId()
  return (
    <form className="workspace-form schedule-decline-form" onSubmit={(event) => {
      event.preventDefault()
      onDecline(new FormData(event.currentTarget).get('reason').trim())
    }}>
      <label className="form-field">
        <span>Reason (optional)</span>
        <textarea name="reason" maxLength={500} rows={3} disabled={busy} aria-describedby={helpId} autoFocus />
      </label>
      <p id={helpId} className="overview-muted">The person who created the schedule can see your reason.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions">
        <Button variant="secondary" disabled={busy} onClick={onBack}>Back</Button>
        <Button variant="danger" type="submit" disabled={busy}>Decline request</Button>
      </div>
    </form>
  )
}

function ScheduleDetails({ actor, contract: current, members, names, onClose, onUpdated }) {
  const dialog = useRef(null)
  const request = useRef(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [actionForm, setActionForm] = useState(null)
  const [feedback, setFeedback] = useState('')
  const now = new Date()
  const beforeEnd = now < new Date(current.end_time)
  const assignedCaregiver = actor?.role === 'CAREGIVER' && actor.id === current.caregiver_id
  const canRespond = assignedCaregiver && current.status === 'PENDING_APPROVAL'
    && current.acceptance_status === 'PENDING' && beforeEnd
  const canAccept = canRespond && isAvailableCaregiver(actor)
  const awaitingHandover = assignedCaregiver && current.status === 'CONFIRMED'
    && current.handover_status === 'PENDING' && beforeEnd
  const canConfirm = awaitingHandover && now >= new Date(current.start_time)
  const canReassign = actor?.role === 'GUARDIAN' && actor.id === current.guardian_id
    && current.status === 'DECLINED' && beforeEnd
  const alternatives = members.filter((member) => isAvailableCaregiver(member) && member.id !== current.caregiver_id)
  const [status, tone] = scheduleStatus(current)
  const requestedBy = names.get(current.guardian_id)
  const showDecline = canRespond && actionForm === 'decline'
  const fields = [
    ['Starts', <time dateTime={current.start_time}>{detailDateTimeFormat.format(new Date(current.start_time))}</time>, CalendarRange],
    ['Ends', <time dateTime={current.end_time}>{detailDateTimeFormat.format(new Date(current.end_time))}</time>, Clock],
    ['Location', current.expected_location, MapPin],
    ['Child', names.get(current.child_id) || 'Child', UsersRound],
    ['Caregiver', names.get(current.caregiver_id) || 'Caregiver', UserRound],
    ['Caregiver response', decisionLabel(current.acceptance_status), ClipboardCheck],
  ]

  useEffect(() => () => request.current?.abort(), [])

  async function updateSchedule(submit, success, fallbackError) {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setSaving(true)
    setError('')
    setFeedback('')
    try {
      const updated = await submit(controller.signal)
      if (!controller.signal.aborted) {
        setActionForm(null)
        setFeedback(success)
        onUpdated(updated)
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof ApiError && typeof failure.detail === 'string'
          ? failure.detail : fallbackError)
      }
    } finally {
      request.current = null
      if (!controller.signal.aborted) setSaving(false)
    }
  }

  function respond(decision, reason) {
    updateSchedule(
      (signal) => respondToSchedule(current, actor.id, decision, reason, signal),
      decision === 'ACCEPTED' ? 'Schedule accepted. You can find it in My assignments.' : 'Request declined.',
      'Couldn’t record this response. Try again.',
    )
  }

  return (
    <Dialog dialogRef={dialog} title={current.activity} busy={saving} onClose={onClose} className="schedule-view-dialog">
      <div className="schedule-detail-summary" data-tone={tone === 'red' || tone === 'amber' ? 'sand' : 'mint'}>
        <div className="schedule-detail-summary-copy">
          <StatusIndicator tone={tone}>{status}</StatusIndicator>
          {requestedBy && <p>Requested by {requestedBy}</p>}
        </div>
        <BannerIllustration icon={CalendarRange} />
      </div>
      {canRespond && (
        <div className="schedule-response-panel">
          <h3>Respond to this request</h3>
          {showDecline ? (
            <DeclineScheduleForm busy={saving} error={error}
              onBack={() => { setActionForm(null); setError('') }}
              onDecline={(reason) => respond('DECLINED', reason)} />
          ) : (
            <>
              <p className="overview-muted">
                {canAccept ? 'You can include a reason when declining.' : 'Set your availability to Available to accept this request.'}
              </p>
              <div className="form-actions">
                <Button variant="secondary" disabled={saving}
                  onClick={() => { setActionForm('decline'); setError('') }}>Decline</Button>
                <Button disabled={saving || !canAccept} onClick={() => respond('ACCEPTED')}>Accept</Button>
              </div>
            </>
          )}
        </div>
      )}
      <dl className="schedule-detail-grid">
        {fields.map(([label, value, Icon]) => (
          <div key={label}>
            <dt><Icon size={15} aria-hidden="true" />{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {current.decline_reason && (
        <div className="schedule-decline-note">
          <h3>Decline reason</h3>
          <p>{current.decline_reason}</p>
        </div>
      )}
      {feedback && <p className="form-success" role="status"><CheckCircle2 size={18} aria-hidden="true" />{feedback}</p>}
      {error && !showDecline && <p className="form-error" role="alert">{error}</p>}
      {awaitingHandover && !canConfirm && <p className="overview-muted">Handover opens at the scheduled start time.</p>}
      {canReassign && !alternatives.length && <p className="overview-muted">No other caregivers are available.</p>}
      {canReassign && actionForm === 'reassign' ? (
        <form className="workspace-form" onSubmit={(event) => {
          event.preventDefault()
          const caregiverId = new FormData(event.currentTarget).get('caregiver_id')
          updateSchedule(
            (signal) => updateScheduleContract(current.id, 'request-caregiver', {
              actor_id: actor.id, caregiver_id: caregiverId, expected_version: current.version,
            }, signal),
            'Request sent to the new caregiver.', 'Couldn’t send the request. Try again.',
          )
        }}>
          <fieldset disabled={saving}><MemberSelect label="Caregiver" name="caregiver_id" members={alternatives} /></fieldset>
          <div className="form-actions">
            <Button variant="secondary" disabled={saving} onClick={() => setActionForm(null)}>Back</Button>
            <Button type="submit" disabled={saving}>Send request</Button>
          </div>
        </form>
      ) : (
        <div className="form-actions">
          <Button variant="secondary" disabled={saving} onClick={() => dialog.current.close()}>Close</Button>
          {canReassign && alternatives.length > 0 && (
            <Button disabled={saving} onClick={() => setActionForm('reassign')}>Request another caregiver</Button>
          )}
          {canConfirm && <Button disabled={saving} onClick={() => updateSchedule(
            (signal) => updateScheduleContract(current.id, 'confirm-handover', { actor_id: actor.id }, signal),
            'Handover confirmed.', 'Couldn’t confirm handover. Try again.',
          )}>Confirm handover</Button>}
        </div>
      )}
    </Dialog>
  )
}

export function TodaySchedule({ actor, members, contracts, onUpdated }) {
  const today = startOfDay(new Date())

  return (
    <Card aria-labelledby="today-title">
      <div className="card-header"><h2 id="today-title">Today's supervision</h2></div>
      <div className="schedule-body">
        <ResourceContent resource={contracts} label="today's supervision">
          {(items) => (
            <ScheduleEntries actor={actor} members={members.data || []}
              contracts={contractsInWindow(items, today, daysFrom(today, 1))}
              view="today" onUpdated={onUpdated} />
          )}
        </ResourceContent>
      </div>
    </Card>
  )
}

export function PersonalSchedules({ actor, members, contracts, onUpdated }) {
  const isCaregiver = actor.role === 'CAREGIVER'
  const title = isCaregiver ? 'My assignments' : 'Upcoming schedules I created'
  const headingId = useId()
  const now = Date.now()

  return (
    <Card aria-labelledby={headingId} className="personal-schedules-card">
      <div className="card-header"><h2 id={headingId}>{title}</h2></div>
      <div className="schedule-body">
        <ResourceContent resource={contracts} label={isCaregiver ? 'assignments' : 'your schedules'}>
          {(items) => {
            const upcoming = items.filter((item) => {
              if (new Date(item.end_time).getTime() <= now) return false
              if (!isCaregiver) return item.guardian_id === actor.id
              return item.caregiver_id === actor.id && item.acceptance_status === 'ACCEPTED'
                && (item.status === 'CONFIRMED' || item.status === 'ACTIVE')
            })
            return (
              <ScheduleEntries actor={actor} members={members.data || []} contracts={upcoming} onUpdated={onUpdated}
                emptyTitle={isCaregiver ? 'No upcoming assignments' : 'No upcoming schedules'}
                emptyDescription={isCaregiver ? 'Schedules you accept appear here.' : 'Schedules you create appear here.'} />
            )
          }}
        </ResourceContent>
      </div>
    </Card>
  )
}

function MemberSelect({ label, name, members }) {
  return (
    <label className="form-field">
      <span>{label}</span>
      <select name={name} required defaultValue={members.length === 1 ? members[0].id : ''}>
        <option value="" disabled>Select {label.toLowerCase()}</option>
        {members.map((member) => (
          <option value={member.id} key={member.id}>{memberDisplayName(member)}</option>
        ))}
      </select>
    </label>
  )
}

function ScheduleForm({ actor, date, members, onClose, onCreated }) {
  const dialog = useRef(null)
  const request = useRef(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const startDefault = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 14)
  const now = new Date()
  if (startOfDay(now).getTime() === startOfDay(date).getTime() && now >= startDefault) {
    startDefault.setTime(now.getTime())
    startDefault.setHours(startDefault.getHours() + 1, 0, 0, 0)
  }
  const endDefault = new Date(startDefault.getTime() + 2 * 60 * 60 * 1000)

  useEffect(() => () => request.current?.abort(), [])

  async function submit(event) {
    event.preventDefault()
    if (request.current) return
    setError('')
    const fields = new FormData(event.currentTarget)
    const activity = fields.get('activity').trim()
    const location = fields.get('expected_location').trim()
    const start = new Date(fields.get('start_time'))
    const end = new Date(fields.get('end_time'))
    if (!activity || !location) {
      setError('Enter an activity and a location.')
      return
    }
    if (!(end > start)) {
      setError('End time must be after start time.')
      return
    }
    if (end <= new Date()) {
      setError('Choose an end time in the future so the caregiver can respond.')
      return
    }

    const controller = new AbortController()
    request.current = controller
    setSaving(true)
    try {
      const contract = await apiRequest('/supervision/contracts', {
        method: 'POST',
        signal: controller.signal,
        body: {
          family_id: familyId,
          child_id: fields.get('child_id'),
          caregiver_id: fields.get('caregiver_id'),
          guardian_id: actor.id,
          activity,
          expected_location: location,
          start_time: start.toISOString(),
          end_time: end.toISOString(),
        },
      })
      if (!controller.signal.aborted) {
        onCreated(contract)
        dialog.current.close()
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof ApiError && typeof failure.detail === 'string'
          ? failure.detail
          : 'Couldn’t save this schedule. Check your entries and connection, then try again.')
      }
    } finally {
      request.current = null
      if (!controller.signal.aborted) setSaving(false)
    }
  }

  return (
    <Dialog dialogRef={dialog} title="Add schedule" description="The caregiver will receive a request to accept or decline."
      className="creation-dialog" busy={saving} onClose={onClose}>
      <ResourceContent resource={members} label="family members">
        {(people) => {
          const children = people.filter(({ role, status }) => role === 'CHILD' && status === 'ACTIVE')
          const caregivers = people.filter(isAvailableCaregiver)
          if (!children.length || !caregivers.length) {
            return <p className="overview-muted">An active child and an available caregiver are needed to create a plan.</p>
          }
          return (
            <form onSubmit={submit} className="workspace-form">
              <fieldset disabled={saving} className="form-grid creation-group">
                <legend><UsersRound size={16} aria-hidden="true" /> People</legend>
                {children.length === 1 ? (
                  <label className="form-field">
                    <span>Child</span>
                    <input value={memberDisplayName(children[0])} readOnly />
                    <input type="hidden" name="child_id" value={children[0].id} />
                  </label>
                ) : (
                  <MemberSelect label="Child" name="child_id" members={children} />
                )}
                <MemberSelect label="Caregiver" name="caregiver_id" members={caregivers} />
              </fieldset>
              <fieldset disabled={saving} className="form-grid creation-group">
                <legend><MapPin size={16} aria-hidden="true" /> Activity and location</legend>
                <label className="form-field">
                  <span>Activity</span>
                  <input name="activity" list="schedule-activities" required
                    placeholder="Choose or type an activity" />
                </label>
                <datalist id="schedule-activities">
                  {suggestedActivities.map((activity) => <option value={activity} key={activity} />)}
                </datalist>
                <label className="form-field">
                  <span>Location</span>
                  <input name="expected_location" required placeholder="School entrance" />
                </label>
              </fieldset>
              <fieldset disabled={saving} className="form-grid creation-group">
                <legend><CalendarRange size={16} aria-hidden="true" /> Supervision window</legend>
                <label className="form-field">
                  <span>Starts</span>
                  <input name="start_time" type="datetime-local" required defaultValue={inputTime(startDefault)} />
                </label>
                <label className="form-field">
                  <span>Ends</span>
                  <input name="end_time" type="datetime-local" required defaultValue={inputTime(endDefault)} />
                </label>
              </fieldset>
              {error && <p role="alert" className="form-error">{error}</p>}
              <div className="form-actions">
                <Button variant="secondary" onClick={() => dialog.current.close()} disabled={saving}>
                  Cancel
                </Button>
                <Button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save schedule'}</Button>
              </div>
            </form>
          )
        }}
      </ResourceContent>
    </Dialog>
  )
}

function CancelScheduleDialog({ contract, onClose, onCancelled }) {
  const dialog = useRef(null)
  const request = useRef(null)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => () => request.current?.abort(), [])

  async function confirmCancellation() {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setCancelling(true)
    setError('')
    try {
      const updated = await apiRequest(`/supervision/contracts/${encodeURIComponent(contract.id)}/cancel`, {
        method: 'POST',
        signal: controller.signal,
      })
      if (!controller.signal.aborted) {
        onCancelled(updated)
        dialog.current.close()
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof ApiError && typeof failure.detail === 'string'
          ? failure.detail
          : 'Couldn’t cancel this schedule. Check your connection and try again.')
      }
    } finally {
      request.current = null
      if (!controller.signal.aborted) setCancelling(false)
    }
  }

  return (
    <Dialog dialogRef={dialog} title="Cancel schedule?"
      description={<><strong>{contract.activity}</strong> will stay in the schedule marked as cancelled.</>}
      busy={cancelling} onClose={onClose}>
      {error && <p role="alert" className="form-error">{error}</p>}
      <div className="form-actions">
        <Button variant="secondary" onClick={() => dialog.current.close()} disabled={cancelling}>
          Keep schedule
        </Button>
        <Button variant="danger" onClick={confirmCancellation} disabled={cancelling}>
          {cancelling ? 'Cancelling…' : 'Cancel schedule'}
        </Button>
      </div>
    </Dialog>
  )
}

export default function SchedulePage({ actor, members, contracts, onUpdated }) {
  const [date, setDate] = useState(() => startOfDay(new Date()))
  const [view, setView] = useState('week')
  const [formOpen, setFormOpen] = useState(false)
  const [cancelTarget, setCancelTarget] = useState(null)
  const [feedback, setFeedback] = useState('')
  const days = view === 'day' ? 1 : 7
  const start = view === 'day' ? date : startOfWeek(date)
  const end = daysFrom(start, days)

  function onCreated(contract) {
    setDate(startOfDay(new Date(contract.start_time)))
    setFeedback('Request sent. You can track the caregiver’s response in the schedule.')
    onUpdated(contract)
  }

  function onCancelled(contract) {
    setFeedback('Schedule cancelled.')
    onUpdated(contract)
  }

  return (
    <div className="section-page">
      <PageBanner title="Supervision schedule" illustration={<BearActivity activity="planning" />}
        description="Plan who will supervise and when.">
        {actor?.role === 'GUARDIAN' && (
          <Button onClick={() => { setFeedback(''); setFormOpen(true) }}>
            <Plus size={18} aria-hidden="true" /> Add schedule
          </Button>
        )}
      </PageBanner>
      {feedback && <p className="form-success" role="status"><CheckCircle2 size={18} aria-hidden="true" />{feedback}</p>}
      <Card aria-labelledby="schedule-period">
        <div className="schedule-toolbar">
          <div className="schedule-date-controls">
            <div className="schedule-arrows">
              <Button variant="secondary" className="icon-button" aria-label={`Previous ${view}`}
                onClick={() => setDate((current) => daysFrom(current, -days))}>
                <ChevronLeft size={18} aria-hidden="true" />
              </Button>
              <Button variant="secondary" className="icon-button" aria-label={`Next ${view}`}
                onClick={() => setDate((current) => daysFrom(current, days))}>
                <ChevronRight size={18} aria-hidden="true" />
              </Button>
            </div>
            <Button variant="secondary" onClick={() => setDate(startOfDay(new Date()))}>Today</Button>
            <h2 id="schedule-period" aria-live="polite">
              {view === 'day' ? dayFormat.format(start) : weekFormat.formatRange(start, daysFrom(end, -1))}
            </h2>
          </div>
          <div className="schedule-view-control" role="group" aria-label="Calendar view">
            <button type="button" aria-pressed={view === 'day'} onClick={() => setView('day')}>Day</button>
            <button type="button" aria-pressed={view === 'week'} onClick={() => setView('week')}>Week</button>
          </div>
        </div>
        <div className="schedule-body">
          <ResourceContent resource={contracts} label="the schedule">
            {(items) => (
              <ScheduleEntries actor={actor} members={members.data || []} contracts={contractsInWindow(items, start, end)}
                view={view} onUpdated={onUpdated}
                onCancel={(contract) => { setFeedback(''); setCancelTarget(contract) }} />
            )}
          </ResourceContent>
        </div>
      </Card>
      {formOpen && actor?.role === 'GUARDIAN' && (
        <ScheduleForm actor={actor} date={date} members={members}
          onClose={() => setFormOpen(false)} onCreated={onCreated} />
      )}
      {cancelTarget && (
        <CancelScheduleDialog contract={cancelTarget} onClose={() => setCancelTarget(null)}
          onCancelled={onCancelled} />
      )}
    </div>
  )
}
