import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, ClipboardCheck } from 'lucide-react'
import { apiRequest, ApiError } from './api.js'
import { Button, Card, Dialog, EmptyState, PageBanner, ResourceContent, StatusIndicator } from './components/ui.jsx'
import { isAvailableCaregiver, loadScheduleRequests, memberDisplayName, respondToSchedule, scheduleRefreshMs } from './familyApi.js'
import { DeclineScheduleForm, PersonalSchedules } from './Schedule.jsx'
import { useResource } from './useResource.js'
import { BearActivity } from './components/BearIllustration.jsx'

const requestTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

function replacementResponsePath(replacement, positive) {
  return replacement.approval_type === 'GUARDIAN'
    ? `/approvals/${encodeURIComponent(replacement.id)}/${positive ? 'approve' : 'reject'}`
    : `/proposals/${encodeURIComponent(replacement.proposal_id)}/${positive ? 'accept' : 'decline'}`
}

function DeclineRequest({ request, onClose, onDecline, saving, error }) {
  const dialog = useRef(null)
  return (
    <Dialog dialogRef={dialog} title="Decline schedule request?"
      description={request.contract.activity} busy={saving} onClose={onClose}>
      <DeclineScheduleForm busy={saving} error={error} onBack={() => dialog.current.close()} onDecline={onDecline} />
    </Dialog>
  )
}

function ScheduleRequests({ actor, members, onUpdated }) {
  const load = useCallback((signal) => loadScheduleRequests(actor.id, signal), [actor.id])
  const requests = useResource(load, { refreshMs: scheduleRefreshMs })
  const inFlight = useRef(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [declineTarget, setDeclineTarget] = useState(null)
  const names = new Map((members.data || []).map((member) => [member.id, memberDisplayName(member)]))

  useEffect(() => () => inFlight.current?.abort(), [])

  async function respond(request, positive, reason) {
    if (inFlight.current) return
    const controller = new AbortController()
    const { contract, replacement_approval: replacement } = request
    inFlight.current = controller
    setSaving(true)
    setError('')
    setFeedback('')
    try {
      const result = replacement
        ? await apiRequest(replacementResponsePath(replacement, positive), {
          method: 'POST', signal: controller.signal, body: { actor_id: actor.id },
        })
        : await respondToSchedule(contract, actor.id, positive ? 'ACCEPTED' : 'DECLINED', reason, controller.signal)
      if (!controller.signal.aborted) {
        const updated = replacement ? result.replacement_contract : result
        if (updated) onUpdated(updated)
        setDeclineTarget(null)
        setFeedback(replacement
          ? 'Response recorded.'
          : (positive ? 'Schedule accepted. You can find it in My assignments.' : 'Request declined.'))
        requests.retry()
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof ApiError && typeof failure.detail === 'string'
          ? failure.detail : 'Couldn’t record this response. Try again.')
        if (failure instanceof ApiError && failure.status === 409) requests.retry()
      }
    } finally {
      inFlight.current = null
      if (!controller.signal.aborted) setSaving(false)
    }
  }

  return (
    <>
      {feedback && <p className="form-success" role="status"><CheckCircle2 size={18} aria-hidden="true" />{feedback}</p>}
      {error && !declineTarget && <p className="form-error" role="alert">{error}</p>}
      <Card aria-labelledby="incoming-requests-title" className="members-card approvals-card">
        <h2 id="incoming-requests-title" className="schedule-queue-title">Pending requests</h2>
        <ResourceContent resource={requests} label="schedule requests">
          {(items) => items.length ? (
            <ul className="schedule-entries">
              {items.map((request) => {
                const { contract, replacement_approval: replacement } = request
                const isGuardian = request.approval_type === 'GUARDIAN'
                const acceptanceUnavailable = !replacement && !isAvailableCaregiver(actor)
                const caregiver = names.get(request.proposed_caregiver_id || contract.caregiver_id) || 'Caregiver'
                const child = names.get(contract.child_id) || 'Child'
                return (
                  <li className="schedule-entry" key={replacement?.id || contract.id}>
                    <div className="schedule-entry-main">
                      <h3>{contract.activity}</h3>
                      <StatusIndicator tone="amber">{replacement ? 'Replacement request' : 'Caregiver request'}</StatusIndicator>
                    </div>
                    {acceptanceUnavailable && (
                      <p className="overview-muted">Set your availability to Available to accept this request.</p>
                    )}
                    <div className="schedule-details">
                      <span>{caregiver} with {child}</span>
                      <span>{requestTime.formatRange(new Date(contract.start_time), new Date(contract.end_time))}</span>
                      <span>{contract.expected_location}</span>
                      {!replacement && names.has(contract.guardian_id) && (
                        <span>Requested by {names.get(contract.guardian_id)}</span>
                      )}
                    </div>
                    <div className="form-actions">
                      <Button variant="secondary" disabled={saving} onClick={() => {
                        if (replacement) {
                          respond(request, false)
                        } else {
                          setError('')
                          setDeclineTarget(request)
                        }
                      }}>
                        {isGuardian ? 'Reject' : 'Decline'}
                      </Button>
                      <Button disabled={saving || acceptanceUnavailable} onClick={() => respond(request, true)}>
                        {isGuardian ? 'Approve' : 'Accept'}
                      </Button>
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : (
            <EmptyState icon={ClipboardCheck} tone="sage" title="No pending requests"
              description={actor.role === 'CAREGIVER'
                ? 'New requests sent to you will appear here. Find accepted schedules in My assignments.'
                : 'Replacement proposals awaiting your approval will appear here.'} />
          )}
        </ResourceContent>
      </Card>
      {declineTarget && (
        <DeclineRequest request={declineTarget} saving={saving} error={error}
          onClose={() => { setDeclineTarget(null); setError('') }}
          onDecline={(reason) => respond(declineTarget, false, reason)} />
      )}
    </>
  )
}

export default function Approvals({ actor, members, contracts, onUpdated }) {
  return (
    <div className="section-page">
      <PageBanner title="Approvals" illustration={<BearActivity activity="reviewing" />} tone="sage"
        description={actor ? `Schedule requests for ${memberDisplayName(actor)}.`
          : 'Respond to supervision and caregiver requests.'} />
      {actor && actor.role !== 'CHILD' ? (
        <ScheduleRequests key={`incoming:${actor.id}`} actor={actor} members={members} onUpdated={onUpdated} />
      ) : (
        <Card>
          <ResourceContent resource={members} label="family members">
            {() => <EmptyState icon={ClipboardCheck} tone="sage" title="Select a guardian or caregiver"
              description="Use the preview picker to see their requests." />}
          </ResourceContent>
        </Card>
      )}
      {(actor?.role === 'GUARDIAN' || actor?.role === 'CAREGIVER') && (
        <PersonalSchedules key={`personal:${actor.id}`} actor={actor} members={members}
          contracts={contracts} onUpdated={onUpdated} />
      )}
    </div>
  )
}
