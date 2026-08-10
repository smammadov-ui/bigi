import React, { useState } from 'react';
import CandidatePicker from './CandidatePicker.jsx';

const MATCHED_BY_LABEL = {
  manual: 'operator selection',
  iban: 'IBAN',
  address: 'address (postcode)',
  dob: 'date of birth',
  none: '—',
};

const IDENTIFIED_BY_LABEL = {
  manual: 'manual UUID',
  ticket_uuid: 'ticket UUID',
  wallet_iban: 'wallet IBAN (ticket listed several UUIDs)',
  register_number: 'register number search',
  iban: 'IBAN search',
  name: 'name search',
};

const OUTCOME_BADGE = {
  MATCH: { cls: 'ok', label: 'match' },
  NO_MATCH: { cls: 'warn', label: 'no match' },
  PERSON_VS_COMPANY: { cls: 'warn', label: 'person vs company' },
};

const BUCKET_BADGE = {
  OPEN: 'ok',
  CLOSED: 'warn',
  CLOSING: 'warn',
  RESTRICTED: 'warn',
  ONBOARDING: 'warn',
  UNKNOWN: '',
};

// The resolved + confirmed account (rail card). When the backend could not
// uniquely resolve it (needs_selection), show the candidate picker /
// manual-UUID re-submit. Once resolved, also show the confirmation outcome
// (IBAN / address / DOB rule) and the BO account status bucket.
export default function AccountPanel({ account, onPick, busy }) {
  const [showReasons, setShowReasons] = useState(false);
  if (!account) return null;
  const resolved = !!account.company_uuid;
  const outcome = OUTCOME_BADGE[account.outcome];

  return (
    <div className="rail-card">
      <div className="rail-head">
        <span className="rail-label">Account</span>
        {resolved && outcome && (
          <span className={`badge ${outcome.cls}`}>{outcome.label}</span>
        )}
        {resolved ? (
          <span className="badge ok">resolved</span>
        ) : (
          <span className="badge warn">needs selection</span>
        )}
      </div>

      {account.error && <div className="banner err">{account.error}</div>}

      {resolved && account.outcome === 'NO_MATCH' && (
        <div className="banner warn">
          <div>
            A company was <strong>identified</strong> by search, but it is{' '}
            <strong>not confirmed</strong> as the debtor — the case is treated
            as NO MATCH (Scenario 4).
          </div>
        </div>
      )}

      {resolved && (
        <>
          <div className="rail-name">{account.business_name || '—'}</div>
          <div className="rail-uuid">{account.company_uuid}</div>
          <div className="rail-note">
            {account.identified_by
              ? `identified by ${IDENTIFIED_BY_LABEL[account.identified_by] || account.identified_by}; `
              : ''}
            confirmed by {MATCHED_BY_LABEL[account.matched_by] || account.matched_by}
          </div>

          <div className="pf-list" style={{ marginTop: 8 }}>
            <div>
              <div className="pk">type</div>
              <div className="pv">{account.account_type || '—'}</div>
            </div>
            <div>
              <div className="pk">status</div>
              <div className="pv">
                {account.account_status || '—'}
                {account.status_bucket && account.status_bucket !== 'UNKNOWN' && (
                  <>
                    {' '}
                    <span className={`badge ${BUCKET_BADGE[account.status_bucket] || ''}`}>
                      {account.status_bucket}
                    </span>
                  </>
                )}
              </div>
            </div>
            {account.address_check && account.address_check.grade !== 'unknown' && (
              <div>
                <div className="pk">address check</div>
                <div className="pv">
                  <span className={`badge ${account.address_check.grade === 'strong' ? 'ok' : account.address_check.grade === 'mismatch' ? 't2' : 'warn'}`}>
                    {account.address_check.grade}
                  </span>{' '}
                  {account.address_check.detail}
                  <div className="muted small" style={{ marginTop: 4 }}>
                    ticket: {account.address_check.ticket || '—'}
                    <br />
                    account: {account.address_check.account || '—'}
                  </div>
                </div>
              </div>
            )}
            {account.seized_iban && (
              <div>
                <div className="pk">seized IBAN</div>
                <div className="pv">
                  {account.seized_iban}
                  {account.seized_iban_source === 'main_wallet' ? ' (from Main wallet)' : ''}
                </div>
              </div>
            )}
          </div>

          {account.reasons && account.reasons.length > 0 && (
            <>
              <button
                className="btn small"
                style={{ marginTop: 8 }}
                onClick={() => setShowReasons((v) => !v)}
              >
                {showReasons ? 'Hide' : 'Why?'}
              </button>
              {showReasons && (
                <div className="muted small" style={{ marginTop: 6 }}>
                  {account.reasons.map((r, i) => (
                    <div key={i}>· {r}</div>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}

      {account.needs_selection && (
        <CandidatePicker candidates={account.candidates} onPick={onPick} busy={busy} />
      )}
    </div>
  );
}
