import { useCallback } from 'react'
import { Bell, ChevronRight, ClipboardCheck, UsersRound } from 'lucide-react'
import { Link } from 'react-router'
import { apiRequest } from './api.js'
import { Card, ResourceContent, StatusIndicator } from './components/ui.jsx'
import { familyPath, isAvailableCaregiver, loadScheduleRequests, memberDisplayName, scheduleRefreshMs } from './familyApi.js'
import { GuardianNotices } from './Notifications.jsx'
import { useResource } from './useResource.js'

async function loadFamily(signal) {
  const family = await apiRequest(familyPath, { signal })
  if (typeof family?.name !== 'string') throw new Error('Expected a family from the API.')
  return family
}

function OverviewCard({ id, title, icon: Icon, to, children }) {
  return (
    <Card aria-labelledby={id} className={to ? 'overview-linked-card' : undefined}>
      <div className="card-header overview-data-header">
        <Icon size={19} strokeWidth={1.8} aria-hidden="true" />
        <h2 id={id}>
          {to ? (
            <Link to={to} className="overview-card-link">
              {title} <ChevronRight size={17} aria-hidden="true" />
            </Link>
          ) : title}
        </h2>
      </div>
      <div className="overview-data-body">{children}</div>
    </Card>
  )
}

function FamilyCard({ members }) {
  const family = useResource(loadFamily)

  return (
    <OverviewCard id="family-caregivers-title" title="Family & caregivers" icon={UsersRound}>
      <ResourceContent resource={family} label="family">
        {({ name }) => <p className="overview-family-name">{name === 'Demo Family' ? 'Your family' : name}</p>}
      </ResourceContent>
      <ResourceContent resource={members} label="caregivers">
        {(people) => {
          const caregivers = people.filter(({ role }) => role === 'CAREGIVER')
          return caregivers.length ? (
            <ul className="overview-list">
              {caregivers.map((caregiver) => (
                <li className="overview-caregiver" key={caregiver.id}>
                  <span>{memberDisplayName(caregiver)}</span>
                  <StatusIndicator tone={isAvailableCaregiver(caregiver) ? 'teal' : 'amber'}>
                    {isAvailableCaregiver(caregiver) ? 'Available' : 'Unavailable'}
                  </StatusIndicator>
                </li>
              ))}
            </ul>
          ) : <p className="overview-muted">No caregivers are listed for this family.</p>
        }}
      </ResourceContent>
    </OverviewCard>
  )
}

function PendingRequests({ actor }) {
  const load = useCallback((signal) => loadScheduleRequests(actor.id, signal), [actor.id])
  const requests = useResource(load, { refreshMs: scheduleRefreshMs })
  const isGuardian = actor.role === 'GUARDIAN'

  return (
    <ResourceContent resource={requests} label="pending requests">
      {(items) => items.length ? (
        <>
          <p className="overview-count">{items.length}</p>
          <p className="overview-muted">
            {items.length === 1 ? 'Request' : 'Requests'} awaiting {isGuardian ? 'your approval' : 'your response'}
          </p>
        </>
      ) : <p className="overview-muted">No pending requests.</p>}
    </ResourceContent>
  )
}

export default function OverviewData({ actor, members }) {
  return (
    <div className="overview-data-grid">
      <FamilyCard members={members} />
      <OverviewCard id="pending-requests-title" title="Pending requests" icon={ClipboardCheck} to="/approvals">
        {actor && actor.role !== 'CHILD' ? <PendingRequests key={actor.id} actor={actor} /> : (
          <p className="overview-muted">Select a guardian or caregiver to see their requests.</p>
        )}
      </OverviewCard>
      <OverviewCard id="guardian-notices-title" title="Guardian notices" icon={Bell} to="/notifications">
        {actor?.role === 'GUARDIAN' ? <GuardianNotices key={actor.id} guardianId={actor.id} /> : (
          <p className="overview-muted">Select a guardian to view their notices.</p>
        )}
      </OverviewCard>
    </div>
  )
}
