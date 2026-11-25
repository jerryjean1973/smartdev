'use client';

import {useEffect, useState, type FormEvent} from 'react';

type Domain = {hostname: string; challenge: string; checked_at: string | null; verified_at: string | null};

export default function ProjectDomains({projectId}: {projectId: string}) {
  const base = '/api/projects/' + projectId + '/domains';
  const [domains, setDomains] = useState<Domain[]>();
  const [hostname, setHostname] = useState('');
  const [expanded, setExpanded] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch(base, {signal: controller.signal, cache: 'no-store'}).then(async response => {
      if (!response.ok) throw Error('Could not load domains.');
      setDomains(await response.json());
    }).catch(() => { if (!controller.signal.aborted) setError('Could not load domains.'); });
    return () => controller.abort();
  }, [base]);

  async function mutate(method: string, suffix: string, payload?: {hostname: string}) {
    setBusy(true);
    setError('');
    try {
      const response = await fetch(base + suffix, {
        method,
        ...(payload && {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}),
      });
      if (!response.ok) {
        const detail = (await response.json().catch(() => ({}))).detail;
        throw Error(typeof detail === 'string' ? detail : 'Domain request failed.');
      }
      if (method === 'DELETE') {
        setDomains(previous => previous?.filter(domain => '/' + encodeURIComponent(domain.hostname) !== suffix));
        setExpanded('');
      } else {
        const domain: Domain = await response.json();
        setDomains(previous => [...(previous || []).filter(item => item.hostname !== domain.hostname), domain]
          .sort((a, b) => a.hostname.localeCompare(b.hostname)));
        setExpanded(domain.hostname);
        if (payload) setHostname('');
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Domain request failed.');
    } finally {
      setBusy(false);
    }
  }

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void mutate('POST', '', {hostname});
  }

  return <div className="wb-content">
    <h2>Domains</h2>
    <section className="panel">
      <h3>Use a domain you already own</h3>
      <form onSubmit={add}>
        <label>Domain or subdomain<input required maxLength={254} placeholder="app.example.com"
          value={hostname} onChange={event => setHostname(event.target.value)} /></label>
        <button disabled={busy}>Next</button>
      </form>
    </section>
    {error && <p role="alert">{error}</p>}
    {domains?.length === 0 && <p>No domains have been reserved.</p>}
    {domains?.map(domain => {
      const path = '/' + encodeURIComponent(domain.hostname);
      return <section className="panel" key={domain.hostname}>
        <h3 style={{overflowWrap: 'anywhere'}}>{domain.hostname}</h3>
        <p>{domain.verified_at ? 'Ownership verified' : 'Ownership pending'} · Routing inactive</p>
        <button aria-expanded={expanded === domain.hostname}
          onClick={() => setExpanded(expanded === domain.hostname ? '' : domain.hostname)}>Manage</button>
        {expanded === domain.hostname && <>
          <p>At your DNS provider, create a TXT record with these values.</p>
          <dl style={{overflowWrap: 'anywhere'}}>
            <dt>Name</dt><dd><code>_smartdev-verification.{domain.hostname}</code></dd>
            <dt>Value</dt><dd><code>{domain.challenge}</code></dd>
          </dl>
          {domain.checked_at && <p>Last checked {new Date(domain.checked_at).toLocaleString()}</p>}
          <p>Domain ownership verification is available. Release routing and HTTPS activation are pending.</p>
          <div className="wb-actions">
            <button disabled={busy} onClick={() => void mutate('POST', path + '/verify')}>Verify ownership</button>
            <button disabled={busy} onClick={() => void mutate('DELETE', path)}>Remove reservation</button>
          </div>
        </>}
      </section>;
    })}
  </div>;
}
