import { useCallback } from 'react'
import { Bell } from 'lucide-react'
import { Card, EmptyState, PageBanner, ResourceContent } from './components/ui.jsx'
import { loadList, memberDisplayName } from './familyApi.js'
import { useResource } from './useResource.js'
import { BearActivity } from './components/BearIllustration.jsx'

const noticeTitles = {
  MISSED_HANDOVER: 'Missed handover',
  UNRESOLVED_CANCELLATION: 'Unresolved cancellation',
  REPLACEMENT_PROPOSAL_FAILED: 'Replacement proposal failed',
}

const noticeDate = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

export function GuardianNotices({ guardianId, heading: Heading = 'h3', empty }) {
  const load = useCallback((signal) =>
    loadList(`/guardians/${encodeURIComponent(guardianId)}/notifications`, signal), [guardianId])
  const notices = useResource(load)

  return (
    <ResourceContent resource={notices} label="guardian notices">
      {(items) => items.length ? (
        <ul className="guardian-notice-list">
          {items.map((notice) => (
            <li className="guardian-notice" key={notice.id}>
              <Heading>{noticeTitles[notice.kind] || 'Supervision notice'}</Heading>
              <p>{notice.message}</p>
              <time dateTime={notice.created_at}>{noticeDate.format(new Date(notice.created_at))}</time>
            </li>
          ))}
        </ul>
      ) : (empty ?? <p className="overview-muted">No guardian notices.</p>)}
    </ResourceContent>
  )
}

export default function Notifications({ actor }) {
  const isGuardian = actor?.role === 'GUARDIAN'

  return (
    <div className="section-page">
      <PageBanner title="Notifications" illustration={<BearActivity activity="phone" />}
        description={isGuardian ? `Guardian notices for ${memberDisplayName(actor)}.` : 'Review guardian notices.'} />
      <Card as="div" className="notifications-card">
        {isGuardian ? (
          <GuardianNotices key={actor.id} guardianId={actor.id} heading="h2"
            empty={<EmptyState icon={Bell} title="No guardian notices"
              description="Updates about missed handovers and caregiver changes will appear here." />} />
        ) : (
          <EmptyState icon={Bell} title="Select a guardian to view notices"
            description="Use Preview as to choose a guardian." />
        )}
      </Card>
    </div>
  )
}
