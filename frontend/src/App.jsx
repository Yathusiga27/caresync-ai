import { useEffect, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Bell,
  CalendarDays,
  CalendarRange,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  FlaskConical,
  HeartHandshake,
  House,
  MessageCircle,
  UsersRound,
} from 'lucide-react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router'
import { Button, Card, EmptyState, StatusIndicator } from './components/ui.jsx'

const sections = [
  { path: '/', label: 'Overview', title: 'Family overview', icon: House },
  {
    path: '/schedule',
    label: 'Supervision Schedule',
    shortLabel: 'Schedule',
    title: 'Supervision schedule',
    icon: CalendarRange,
    description: 'See planned supervision and upcoming handovers.',
    emptyTitle: 'Schedule data pending',
    emptyDescription: 'Daily and weekly views will use supervision contracts from FastAPI.',
  },
  {
    path: '/approvals',
    label: 'Approvals',
    title: 'Approvals',
    icon: ClipboardCheck,
    description: 'Review replacement proposals and see who still needs to respond.',
    emptyTitle: 'Approval requests pending integration',
    emptyDescription: 'Guardian decisions and caregiver responses will appear here.',
    shortcut: 'Review requests and responses',
    accent: 'amber',
  },
  {
    path: '/chat',
    label: 'CareSync AI Chat',
    shortLabel: 'AI Chat',
    title: 'CareSync AI Chat',
    icon: MessageCircle,
    description: 'Ask about supervision plans and caregiver availability.',
    emptyTitle: 'Chat connection pending',
    emptyDescription: 'This space will connect to the local Ollama assistant through FastAPI.',
    shortcut: 'Ask about supervision plans',
    accent: 'lavender',
  },
  {
    path: '/notifications',
    label: 'Notifications',
    shortLabel: 'Notices',
    title: 'Notifications',
    icon: Bell,
    description: 'Review guardian notices and supervision activity.',
    emptyTitle: 'Inbox data pending',
    emptyDescription: 'Escalation notices and coordination activity will appear here.',
    shortcut: 'Review family notices',
    accent: 'teal',
  },
]

const demoRoles = ['Mother', 'Father', 'Grandmother']
const dateFormatter = new Intl.DateTimeFormat('en', {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
})

function Brand() {
  return (
    <Link to="/" className="brand" aria-label="CareSync AI overview">
      <span className="brand-mark"><HeartHandshake size={23} strokeWidth={1.85} aria-hidden="true" /></span>
      <span>CareSync <span className="brand-ai">AI</span></span>
    </Link>
  )
}

function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand"><Brand /></div>
      <nav className="navigation" aria-label="Main navigation">
        {sections.map(({ path, label, shortLabel, icon: Icon }) => (
          <NavLink key={path} to={path} end aria-label={label} className="nav-link">
            <Icon size={19} strokeWidth={1.8} aria-hidden="true" />
            <span className="nav-label">{label}</span>
            <span className="nav-short-label" aria-hidden="true">{shortLabel || label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="demo-note">
        <FlaskConical size={17} strokeWidth={1.8} aria-hidden="true" />
        <div>
          <p>Local demo</p>
          <span>Demo roles are for preview only.</span>
        </div>
      </div>
    </aside>
  )
}

function Header({ role, onRoleChange }) {
  return (
    <header className="workspace-header">
      <div className="mobile-brand"><Brand /></div>
      <div className="header-date">
        <CalendarDays size={17} strokeWidth={1.8} aria-hidden="true" />
        {dateFormatter.format(new Date())}
      </div>
      <label className="role-control">
        <span className="role-label">Demo role</span>
        <span className="role-select">
          <span className="role-avatar" aria-hidden="true">{role[0]}</span>
          <select
            aria-describedby="demo-role-help"
            value={role}
            onChange={(event) => onRoleChange(event.target.value)}
          >
            {demoRoles.map((option) => <option key={option}>{option}</option>)}
          </select>
          <ChevronDown className="role-chevron" size={15} aria-hidden="true" />
        </span>
      </label>
      <span id="demo-role-help" className="sr-only">Changes the local demo role. It grants no permissions.</span>
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
      <span className="planner-float planner-float-people"><UsersRound size={21} strokeWidth={1.8} /></span>
      <span className="planner-float planner-float-check"><Check size={25} strokeWidth={2.2} /></span>
    </div>
  )
}

function Overview() {
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

      <div className="overview-grid">
        <Card aria-labelledby="today-title">
          <div className="card-header">
            <h2 id="today-title">Today’s supervision</h2>
            <StatusIndicator tone="teal">Preview</StatusIndicator>
          </div>
          <EmptyState
            icon={CalendarRange}
            title="Supervision data pending"
            description="Scheduled plans and caregiver assignments will appear when this dashboard connects to FastAPI."
            heading="h3"
          />
        </Card>
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

function SectionPage({ section }) {
  return (
    <div className="section-page">
      <div className="page-heading">
        <h1>{section.title}</h1>
        <p>{section.description}</p>
      </div>
      <Card>
        <EmptyState icon={section.icon} title={section.emptyTitle} description={section.emptyDescription} />
      </Card>
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

export default function App() {
  const [demoRole, setDemoRole] = useState('Mother')
  const { pathname } = useLocation()

  useEffect(() => {
    const currentPath = pathname.replace(/\/+$/, '') || '/'
    const title = sections.find(({ path }) => path === currentPath)?.title
    document.title = `${title || 'Page not found'} | CareSync AI`
  }, [pathname])

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">Skip to content</a>
      <Header role={demoRole} onRoleChange={setDemoRole} />
      <Sidebar />
      <main id="main-content" className="workspace-content" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Overview />} />
          {sections.slice(1).map((section) => (
            <Route key={section.path} path={section.path} element={<SectionPage section={section} />} />
          ))}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}
