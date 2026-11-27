import { useEffect, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Bell,
  Bot,
  CalendarDays,
  CalendarRange,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ClipboardCheck,
  FlaskConical,
  HeartHandshake,
  House,
  Monitor,
  Moon,
  Sun,
  PanelLeftClose,
  PanelLeftOpen,
  CircleUserRound,
  UsersRound,
} from 'lucide-react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router'
import Approvals from './Approvals.jsx'
import FamilyMembers from './FamilyMembers.jsx'
import Notifications from './Notifications.jsx'
import OverviewData from './OverviewData.jsx'
import AssistantChat from './AssistantChat.jsx'
import { AccountAccess, AccountSettings } from './Account.jsx'
import SchedulePage, { PersonalSchedules, TodaySchedule } from './Schedule.jsx'
import { Button, Card } from './components/ui.jsx'
import { Bear } from './components/BearIllustration.jsx'
import { loadFamilyContracts, loadFamilyMembers, memberDisplayName, roleLabels, scheduleRefreshMs } from './familyApi.js'
import { useResource } from './useResource.js'

const sections = [
  { path: '/', label: 'Overview', title: 'Family overview', icon: House },
  { path: '/family', label: 'Family members', shortLabel: 'Family', title: 'Family members', icon: UsersRound },
  {
    path: '/schedule',
    label: 'Supervision Schedule',
    shortLabel: 'Schedule',
    title: 'Supervision schedule',
    icon: CalendarRange,
  },
  {
    path: '/approvals',
    label: 'Approvals',
    title: 'Approvals',
    icon: ClipboardCheck,
    description: 'Review replacement proposals and see who still needs to respond.',
    shortcut: 'Review requests and responses',
    accent: 'amber',
  },
  {
    path: '/chat',
    label: 'CareSync AI Chat',
    shortLabel: 'AI Chat',
    title: 'CareSync AI Chat',
    icon: Bot,
    shortcut: 'Ask about supervision plans',
    accent: 'lavender',
  },
  {
    path: '/notifications',
    label: 'Notifications',
    shortLabel: 'Notices',
    title: 'Notifications',
    icon: Bell,
    shortcut: 'Review family notices',
    accent: 'teal',
  },
  { path: '/account', label: 'My account', shortLabel: 'Account', title: 'My account', icon: CircleUserRound },
]

const dateFormatter = new Intl.DateTimeFormat('en', {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
})

function Brand() {
  return (
    <Link to="/" className="brand" aria-label="CareSync AI overview">
      <span className="brand-mark"><HeartHandshake size={23} strokeWidth={1.85} aria-hidden="true" /></span>
      <span className="brand-name">CareSync <span className="brand-ai">AI</span></span>
    </Link>
  )
}

function Sidebar({ collapsed, onToggle }) {
  const toggleLabel = collapsed ? 'Expand preview sidebar' : 'Minimize preview sidebar'
  const DesktopToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose
  const MobileToggleIcon = collapsed ? ChevronDown : ChevronUp

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-brand"><Brand /></div>
        <Button variant="secondary" className="sidebar-toggle" onClick={onToggle}
          aria-label={toggleLabel} title={toggleLabel} aria-expanded={!collapsed}
          aria-controls="preview-navigation">
          <DesktopToggleIcon className="sidebar-toggle-desktop" size={18} aria-hidden="true" />
          <MobileToggleIcon className="sidebar-toggle-mobile" size={18} aria-hidden="true" />
        </Button>
      </div>
      <nav id="preview-navigation" className="navigation" aria-label="Main navigation">
        {sections.map(({ path, label, shortLabel, icon: Icon }) => (
          <NavLink key={path} to={path} end aria-label={label} title={collapsed ? label : undefined} className="nav-link">
            <Icon size={19} strokeWidth={1.8} aria-hidden="true" />
            <span className="nav-label">{label}</span>
            <span className="nav-short-label" aria-hidden="true">{shortLabel || label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div className="sidebar-art" aria-hidden="true">
          <span className="sidebar-art-calendar"><CalendarDays size={34} strokeWidth={1.55} /></span>
          <span className="sidebar-art-care"><HeartHandshake size={18} strokeWidth={1.7} /></span>
          <span className="sidebar-art-check"><Check size={18} strokeWidth={2.15} /></span>
        </div>
        <div className="preview-note">
          <FlaskConical size={17} strokeWidth={1.8} aria-hidden="true" />
          <div>
            <p>Local preview</p>
            <span>Choose a family member to test their workflow.</span>
          </div>
        </div>
      </div>
    </aside>
  )
}

function AppearancePicker() {
  const [mode, setMode] = useState(() => document.documentElement.dataset.appearance || 'system')
  const Icon = { light: Sun, dark: Moon, system: Monitor }[mode]

  function changeMode(event) {
    const next = event.target.value
    document.documentElement.dataset.appearance = next
    setMode(next)
    try {
      localStorage.setItem('caresync-appearance', next)
    } catch {
      // The selection still applies to this tab when storage is unavailable.
    }
  }

  return (
    <label className="appearance-picker" title={`Appearance (${mode})`}>
      <Icon size={17} aria-hidden="true" />
      <select aria-label="Appearance" value={mode} onChange={changeMode}>
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  )
}

function Header({ actor, people, onActorChange }) {
  return (
    <header className="workspace-header">
      <div className="mobile-brand"><Brand /></div>
      <div className="header-date">
        <CalendarDays size={17} strokeWidth={1.8} aria-hidden="true" />
        {dateFormatter.format(new Date())}
      </div>
      <AppearancePicker />
      <label className="role-control">
        <span className="role-label">Preview as</span>
        <span className="role-select">
          <span className="role-avatar" aria-hidden="true">{actor ? memberDisplayName(actor)[0] : '–'}</span>
          <select
            aria-describedby="preview-role-help"
            value={actor?.id || ''}
            onChange={(event) => onActorChange(event.target.value)}
            disabled={!people.length}
          >
            {!people.length && <option value="">No family member</option>}
            {people.map((member) => (
              <option key={member.id} value={member.id}>{memberDisplayName(member)} · {roleLabels[member.role]}</option>
            ))}
          </select>
          <ChevronDown className="role-chevron" size={15} aria-hidden="true" />
        </span>
      </label>
      <span id="preview-role-help" className="sr-only">Select a local preview identity.</span>
    </header>
  )
}

function PlannerArt() {
  return (
    <div className="planner-art" aria-hidden="true">
      <div className="planner-tile">
        <span className="planner-calendar"><CalendarDays size={25} strokeWidth={1.8} /></span>
        <span className="planner-line planner-line-long" />
        <span className="planner-line planner-line-short" />
        <div className="planner-checks">
          <span><Check size={14} strokeWidth={2.4} /></span>
          <span><Check size={14} strokeWidth={2.4} /></span>
          <span />
        </div>
      </div>
      <Bear cub />
      <span className="planner-float planner-float-check"><Check size={25} strokeWidth={2.2} /></span>
    </div>
  )
}

function Overview({ actor, members, contracts, onUpdated }) {
  return (
    <div className="overview">
      <section className="hero" aria-labelledby="overview-title">
        <div className="hero-copy">
          <h1 id="overview-title">Family overview</h1>
          <p>See who’s scheduled, plan handovers, and keep track of decisions.</p>
          <Button to="/schedule">View schedule <ArrowRight size={17} aria-hidden="true" /></Button>
        </div>
        <PlannerArt />
      </section>

      <OverviewData actor={actor} members={members} />

      {(actor?.role === 'GUARDIAN' || actor?.role === 'CAREGIVER') && (
        <PersonalSchedules key={actor.id} actor={actor} members={members} contracts={contracts} onUpdated={onUpdated} />
      )}
      <div className="overview-grid">
        <TodaySchedule actor={actor} members={members} contracts={contracts} onUpdated={onUpdated} />
        <Card aria-labelledby="quick-access-title">
          <div className="card-header"><h2 id="quick-access-title">Quick access</h2></div>
          <ul className="quick-links">
            {sections.filter(({ shortcut }) => shortcut).map(({ path, title, icon: Icon, shortcut, accent }) => (
              <li key={path}>
                <Link to={path} className="quick-link">
                  <span className={`shortcut-icon shortcut-${accent}`}>
                    <Icon size={20} strokeWidth={1.8} aria-hidden="true" />
                  </span>
                  <span className="shortcut-copy">
                    <span>{title}</span>
                    <small>{shortcut}</small>
                  </span>
                  <ChevronRight size={17} className="shortcut-arrow" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  )
}

function NotFound() {
  return (
    <div className="section-page">
      <div className="page-heading"><h1>Page not found</h1></div>
      <Button to="/" variant="secondary"><ArrowLeft size={17} aria-hidden="true" /> Back to overview</Button>
    </div>
  )
}

function Workspace({ previewActorId, setPreviewActorId, profiles, saveProfile }) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const members = useResource(loadFamilyMembers)
  const people = (members.data || []).filter(({ status }) => status === 'ACTIVE')
  const actor = people.find(({ id }) => id === previewActorId) ?? people[0]
  const { pathname } = useLocation()
  const currentPath = pathname.replace(/\/+$/, '') || '/'
  const refreshSchedules = currentPath === '/' || currentPath === '/schedule' || currentPath === '/approvals'
  const contracts = useResource(loadFamilyContracts, { refreshMs: refreshSchedules ? scheduleRefreshMs : 0 })

  function onScheduleUpdated(updated) {
    if (contracts.status !== 'ready') {
      contracts.retry()
      return
    }
    contracts.updateData((items) => {
      if (items.some((item) => item.id === updated.id)) {
        return items.map((item) => item.id === updated.id && item.version <= updated.version ? updated : item)
      }
      return [...items, updated].sort((a, b) => new Date(a.start_time) - new Date(b.start_time) || a.id.localeCompare(b.id))
    })
  }

  useEffect(() => {
    const title = sections.find(({ path }) => path === currentPath)?.title
    document.title = `${title || 'Page not found'} | CareSync AI`
  }, [currentPath])

  return (
    <div className="app-shell" data-sidebar-collapsed={sidebarCollapsed}>
      <a href="#main-content" className="skip-link">Skip to content</a>
      <Header actor={actor} people={people} onActorChange={setPreviewActorId} />
      <Sidebar collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((collapsed) => !collapsed)} />
      <main id="main-content" className="workspace-content" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Overview actor={actor} members={members} contracts={contracts} onUpdated={onScheduleUpdated} />} />
          <Route path="/family" element={<FamilyMembers actor={actor} members={members} />} />
          <Route path="/schedule" element={<SchedulePage actor={actor} members={members} contracts={contracts} onUpdated={onScheduleUpdated} />} />
          <Route path="/approvals" element={<Approvals actor={actor} members={members} contracts={contracts} onUpdated={onScheduleUpdated} />} />
          <Route path="/notifications" element={<Notifications actor={actor} />} />
          <Route path="/chat" element={<AssistantChat key={actor?.id || 'guest'} contracts={contracts} members={people} />} />
          <Route path="/account" element={<AccountSettings key={actor?.id || 'guest'} actor={actor}
            members={members} profile={profiles[actor?.id]} onSave={(profile) => saveProfile(actor.id, profile)} />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  const [previewActorId, setPreviewActorId] = useState('guardian-demo')
  const [profiles, setProfiles] = useState({})
  const accessPage = (mode) => <AccountAccess key={mode} mode={mode} brand={<Brand />} appearance={<AppearancePicker />} />
  return (
    <Routes>
      <Route path="/sign-in" element={accessPage('signin')} />
      <Route path="/sign-up" element={accessPage('signup')} />
      <Route path="*" element={<Workspace previewActorId={previewActorId} setPreviewActorId={setPreviewActorId}
        profiles={profiles} saveProfile={(id, profile) => setProfiles((current) => ({ ...current, [id]: profile }))} />} />
    </Routes>
  )
}
