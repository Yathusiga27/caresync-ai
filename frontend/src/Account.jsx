import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowRight, CalendarCheck, Heart, House, KeyRound, Sprout } from 'lucide-react'
import { BearActivity } from './components/BearIllustration.jsx'
import { Link } from 'react-router'
import { Button, Card, PageBanner, ResourceContent } from './components/ui.jsx'
import { memberDisplayName, roleLabels } from './familyApi.js'
import './account.css'

function FamilyHouse() {
  return (
    <div className="family-house" aria-hidden="true">
      <div className="family-house-orbit">
        <span className="family-house-tile"><House /><Heart /></span>
        <span className="family-house-leaf"><Sprout /></span>
        <span className="family-house-date"><CalendarCheck /></span>
      </div>
    </div>
  )
}

export function AccountAccess({ mode, brand, appearance }) {
  const registering = mode === 'signup'
  const title = registering ? 'Create an account' : 'Sign in'
  const [checked, setChecked] = useState(false)
  useEffect(() => { document.title = `${title} | CareSync AI` }, [title])

  return (
    <div className="account-access">
      <header className="account-access-header">{brand}{appearance}</header>
      <main className="account-access-main">
        <div className="account-access-panel">
          <FamilyHouse />
          <section className="account-access-form" aria-labelledby="access-title">
            <h1 id="access-title">{title}</h1>
            <p className="account-hint">Try the form with example details. Account access will use ThunderID once connected.</p>
            <form className="workspace-form" onChange={() => setChecked(false)}
              onSubmit={(event) => { event.preventDefault(); setChecked(true) }}>
              {registering && <label className="form-field">Your name
                <input required autoComplete="name" maxLength={100} pattern={'.*\\S.*'} />
              </label>}
              <label className="form-field">Email address
                <input type="email" required autoComplete="email" maxLength={254} placeholder="you@example.com" />
              </label>
              <Button type="submit">Continue <ArrowRight size={16} aria-hidden="true" /></Button>
              <div role="status">{checked && <p className="account-feedback">
                {registering ? 'Registration details checked.' : 'Email format checked.'} Live {registering ? 'registration' : 'sign-in'} will be available when ThunderID is connected.
              </p>}</div>
            </form>
            <p className="account-switch">{registering ? 'Already have an account? ' : 'New to CareSync? '}
              <Link to={registering ? '/sign-in' : '/sign-up'}>{registering ? 'Sign in' : 'Create an account'}</Link>
            </p>
          </section>
        </div>
        <Link className="account-back" to="/account"><ArrowLeft size={16} aria-hidden="true" /> Back to account preview</Link>
      </main>
    </div>
  )
}

function ProfileForm({ actor, profile, onSave }) {
  const startingName = profile?.name ?? memberDisplayName(actor)
  const startingEmail = profile?.email ?? ''
  const [name, setName] = useState(startingName)
  const [email, setEmail] = useState(startingEmail)
  const [saved, setSaved] = useState(false)
  const edited = name !== startingName || email !== startingEmail

  function save(event) {
    event.preventDefault()
    const next = { name: name.trim(), email: email.trim() }
    onSave(next)
    setName(next.name)
    setEmail(next.email)
    setSaved(true)
  }

  return (
    <form className="workspace-form" onSubmit={save} onChange={() => setSaved(false)}>
      <div className="account-member">
        <span aria-hidden="true">{startingName.charAt(0).toUpperCase()}</span>
        <div><strong>{startingName}</strong><small>{roleLabels[actor.role]} · Local preview</small></div>
      </div>
      <label className="form-field">Preferred name
        <input value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} pattern={'.*\\S.*'} />
      </label>
      <label className="form-field">Contact email (optional)
        <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} maxLength={254} />
      </label>
      <p className="account-hint">Saved edits apply to this member’s account preview until you reload the page.</p>
      <div className="account-actions">
        <Button type="submit" disabled={!edited}>Save preview</Button>
        {edited && <Button variant="secondary" onClick={() => {
          setName(startingName); setEmail(startingEmail); setSaved(false)
        }}>Discard edits</Button>}
      </div>
      <div role="status">{saved && <p className="account-feedback">Profile saved for this preview.</p>}</div>
    </form>
  )
}

export function AccountSettings({ actor, members, profile, onSave }) {
  return (
    <div className="section-page">
      <PageBanner title="My account" illustration={<BearActivity activity="profile" />} description="Manage your profile and sign-in details." />
      <div className="account-columns">
        <Card className="account-section" aria-labelledby="profile-title">
          <h2 id="profile-title">Profile details</h2>
          <ResourceContent resource={members} label="the preview profile">{() => actor
            ? <ProfileForm actor={actor} profile={profile} onSave={onSave} />
            : <p className="account-hint">Add a family member to preview their profile.</p>}
          </ResourceContent>
        </Card>
        <Card className="account-section account-security" aria-labelledby="access-heading">
          <span className="account-key"><KeyRound size={23} aria-hidden="true" /></span>
          <h2 id="access-heading">Account access</h2>
          <p className="account-hint">Explore sign-in and registration while ThunderID is being connected.</p>
          <div className="account-actions">
            <Button to="/sign-in">Sign in <ArrowRight size={16} aria-hidden="true" /></Button>
            <Button variant="secondary" to="/sign-up">Create an account</Button>
          </div>
        </Card>
      </div>
    </div>
  )
}
