import { Bell, CalendarDays, ClipboardList, Contact, MessageCircleMore } from 'lucide-react'
import './bears.css'

export function Bear({ cub = false, children }) {
  return (
    <span className={cub ? 'bear bear-cub' : 'bear'} aria-hidden="true">
      <span className="bear-coat" />
      <span className="bear-paw" />
      <span className="bear-head">
        <span className="bear-eyes" />
        <span className="bear-muzzle" />
      </span>
      {children}
    </span>
  )
}

export function BearFamily() {
  return <div className="bear-family" aria-hidden="true"><Bear /><Bear cub /></div>
}

const propsByActivity = {
  phone: Bell,
  planning: CalendarDays,
  reviewing: ClipboardList,
  chatting: MessageCircleMore,
  profile: Contact,
}

export function BearActivity({ activity }) {
  const Icon = propsByActivity[activity]
  return (
    <div className="bear-activity" data-activity={activity} aria-hidden="true">
      <Bear>
        <span className="bear-accessory"><Icon strokeWidth={1.8} /></span>
      </Bear>
    </div>
  )
}
