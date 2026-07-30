import React from 'react';

// German money formatter for raw numbers in the wallet breakdown (e.g. 6771.29
// -> "6.771,29"). The headline figures arrive pre-formatted from the backend.
function de(n) {
  const x = Number(n);
  if (Number.isNaN(x)) return '—';
  return x.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Account balance (EUR wallets only) + the seizable amount used in the
// declaration (rail card). Funds held under an ongoing seizure live on the
// seizure record (not the bank wallets), so they are shown explicitly.
export default function BalancePanel({ balance }) {
  if (!balance) return null;
  const {
    available_eur_de,
    seizable_eur_de,
    held_eur_de,
    client_total_eur_de,
    breakdown = [],
    non_eur = [],
    error,
  } = balance;
  const haveBalance = available_eur_de != null;

  return (
    <div className="rail-card">
      <div className="rail-label">Balance</div>

      {error && (
        <div className="banner warn">
          {error} — the declaration falls back to the claimed amount.
        </div>
      )}

      <div className="bal-headline">
        <span className="amt">{haveBalance ? `€${available_eur_de}` : '—'}</span>
        <span className="cap">available</span>
      </div>

      <div className="bal-grid">
        {held_eur_de != null && (
          <>
            <span className="k">held under seizure</span>
            <span className="v held">€{held_eur_de}</span>
          </>
        )}
        {seizable_eur_de != null && (
          <>
            <span className="k">seizable (in letter)</span>
            <span className="v">€{seizable_eur_de}</span>
          </>
        )}
        {client_total_eur_de != null && (
          <>
            <span className="k">freely available</span>
            <span className="v">€{client_total_eur_de}</span>
          </>
        )}
      </div>

      {haveBalance && breakdown.length > 0 && (
        <div className="cand-list">
          {breakdown.map((w, i) => (
            <div className="cand" key={w.iban || i}>
              <div className="info">
                <div className="name">
                  {(w.name || 'wallet')} — €{de(w.balance)}
                </div>
                <div className="meta">{w.iban || '—'}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {non_eur.length > 0 && (
        <div className="banner warn">
          Non-EUR wallets excluded from the seizable amount:{' '}
          {non_eur.map((w) => `${de(w.balance)} ${w.currency}`).join(', ')}.
        </div>
      )}
    </div>
  );
}
