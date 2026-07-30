# Design brief — inherited from "mini" (bigi keeps the same UI language)

> Paste this whole file into the claude.ai/design agent. It describes the real
> screens, content, and constraints. When you're happy with the result, the
> engineer will port the visual direction back into the app's React + CSS.

## 1. What this product is

**mini** is an internal desktop tool used by a bank's operations team to produce
**German §840 ZPO "Drittschuldnererklärung"** letters — the formal reply a bank
(as third-party debtor) must send a court when a customer's account is seized.

An operator:
1. Pastes a seizure ticket (or fetches it from Jira),
2. The tool parses it, looks the customer up in the bank's Back-Office (BO),
   checks for competing seizures, and decides a template (**T1** = no other
   ongoing seizure, **T2** = at least one), and
3. Generates an editable German letter the operator reviews, copies into an
   email, and sends.

**Users:** a handful of trained ops specialists, many cases per day, on desktop.
This is a focused power-tool, not a consumer app. It runs in a desktop window
(Tauri/WebView), single column, content max-width ≈ **960–1000px**, centered.

## 2. Design goals

- **Polish & hierarchy.** Today every result is a flat stack of same-weight
  cards. The **Declaration letter is the hero** — it's what the operator acts
  on. Everything else (account, balance, seizure check, parsed fields) is
  supporting reference and should read as secondary.
- **Trust & seriousness.** It generates legal documents. Calm, precise,
  confident. Not playful.
- **Speed & density.** Power users, repeated use. Tight but breathable; scannable.
- **The letter should feel like a document** — paper/margins/typographic rhythm —
  not a raw monospace textbox.
- **Clear states:** loading, empty, error/warning banners, and the T1↔T2 distinction.

## 3. Theme & tokens

Currently a **dark theme** with a pink accent. **Keep dark as the default but
modernize it** (refine contrast, spacing, typographic scale). Optionally also
propose a **light variant** — but dark is primary. Current baseline tokens
(refine these; keep the pink accent as the brand color):

```
--bg:        #0f1419   /* app background */
--bg-elev:   #161b22   /* card surface */
--bg-elev-2: #1c232c   /* inputs, chips */
--border:    #2a323c
--text:      #e6edf3   /* primary text */
--text-dim:  #9aa7b4   /* labels, secondary */
--text-faint:#6b7785   /* hints, placeholders */
--accent:    #ff2e88   /* pink — brand/primary action */
--ok:        #2ecc71   /* green  (T1, success) */
--warn:      #f0b429   /* amber  (T2, warnings) */
--err:       #ff5d5d   /* red    (errors) */
font sans: system UI stack;  font mono: SF Mono / Menlo (used for data & the letter)
card radius 12px, control radius 8px
```

**Implementation constraint:** the app is **plain React + hand-written CSS with
CSS variables** — no Tailwind, no component library. Please keep the design
expressible as a token set + simple components (buttons, cards, badges, banners,
inputs) so it maps cleanly back to CSS variables and small components. Deliver a
**tokens + components sheet** alongside the screens.

## 4. Global components to define

- **Top nav** (sticky): brand wordmark `mini.` (the dot is pink) + label
  "Drittschuldnererklärung", spacer, then nav links **Home** / **Settings**
  (active state uses the accent).
- **Buttons:** `primary` (pink, filled), `default` (subtle outline), `small`
  variant. Disabled + loading (spinner) states.
- **Badges/pills:** `T1` (green), `T2` (amber), `accent` (pink, e.g. "LLM"),
  neutral (e.g. "deterministic fill").
- **Banners:** `error` (red), `warning` (amber), `info` (neutral surface).
- **Form controls:** label + text input, password input, select, textarea;
  focus ring in accent; placeholder in faint; small "hint" text; inline
  checkbox rows.
- **Spinner** and a bottom-center **toast** ("Copied to clipboard").

## 5. Screen 1 — Home

### 5a. Input card (top)
- Header row: title **"Input"** on the left; on the right two small buttons —
  **"Hide paste box" / "Show paste box"** (toggle) and **"↻ New case"**.
- **Paste box** (collapsible): label "Paste seizure ticket", large multiline
  textarea, then a primary **"Generate"** button.
- A divider, then a **"Fetch from Jira"** row: a text input ("e.g. SEIZ-1234 or
  a browse link") that grows, plus **"Fetch"** and **"Browse"** buttons inline.
- **Browse results** (when open): a list of ticket rows — each shows the summary
  (bold) + key (mono, dim) on the left and a small **"Fetch"** button on the right.

### 5b. Results (in THIS order — the letter comes first)
1. **Context banner** (info): the Jira key in bold + " — " + ticket summary.
2. **Alert banner** (only if present): red "Parsing halted: …" or amber warnings.
3. **Declaration — THE HERO CARD.** See 5c.
4. **Account** — customer match: business name, company UUID (mono), "matched
   by" note; if ambiguous, a candidate list to pick from; possible error note.
5. **Balance** — available EUR (big), a per-wallet breakdown, plus "held under
   seizure" and "seizable" amounts.
6. **Seizure check** — a header with the **T1/T2 badge** + "N ongoing
   (Processing)"; info banners for ignored seizures ("own case", "created after
   this case"); then a list of competing seizures (case number, status · date,
   comment).
7. **Parsed fields** — a plain key/value table of everything parsed from the
   ticket (reference detail, least prominent).

### 5c. Declaration editor (design centerpiece)
Make this feel like reviewing a real letter.
- **Header:** "Declaration" title, a **T1/T2** badge, a **composed-by** badge
  ("LLM (openai)" in accent, or "deterministic fill" neutral), and an
  **Edit ⇄ Preview** toggle on the right.
- **Mail subject box:** a labeled, read-only, selectable one/two-line box holding
  the email subject, with a **"Copy subject"** button beside it.
- **Letter preview:** a document-styled block (think a sheet of paper with
  comfortable margins and line-height). Key facts at top are **bold labels**
  (`Gläubiger:`, `Schuldner:`, `Zustellungsdatum bei uns:`, `Pfändungsbetrag:`).
  Body paragraphs in prose. Ongoing seizures render as a **bullet list**.
- **Source coloring:** values pulled from the **Jira ticket are one accent color
  (currently blue)**, values read from **Back-Office are another (currently
  pink)**; a small **legend** ("■ from Jira ticket / ■ from Back-Office") sits
  under the letter. (Design nicer source affordances if you have a better idea —
  the point is the operator can see at a glance where each value came from.)
- **Edit mode:** the preview flips to an editable monospace textarea.
- **Actions:** primary **"Copy"** + **"Download .txt"**.

### 5d. Real sample content (use this, not lorem ipsum)

**Mail subject:**
`Drittschuldnererklärung gemäß § 840 ZPO zum Aktenzeichen 2614/239/24045 - VO 05 - 12619/26 F`

**Letter body (a T2 example — note the bold labels, blue=Jira, pink=BO, and the bullet):**
```
Gläubiger: IKK classic, Eislebener Straße 1, 99086 Erfurt          ← label bold, value blue (Jira)

Schuldner: Thorsten Rosenthal, GGK-Gebäudereinigung                 ← value blue (Jira)

Zustellungsdatum bei uns: 09.07.2026                                ← value blue (Jira)

Pfändungsbetrag: 1.092,75 EUR                                       ← value blue (Jira)

Sehr geehrte Damen und Herren,

unter Bezugnahme auf den uns zugestellten Pfändungs- und Überweisungsbeschluss
geben wir hiermit fristgerecht die folgende Erklärung als Drittschuldner gemäß
§ 840 der Zivilprozessordnung (ZPO) ab:

Der Schuldner unterhält eine Geschäftsbeziehung zu uns. Eine Forderung des
Schuldners gegen uns besteht grundsätzlich. Kundenbeziehung besteht: Ja.

Die gepfändete Forderung besteht derzeit in Höhe von 0,00 EUR.                    ← amount pink (BO)

Die gepfändete Forderung betrifft Guthaben auf Konten, die keine
Pfändungsschutzkonten im Sinne des § 850k ZPO sind. Derzeit ist kein pfändbares
Guthaben verfügbar, das den geltenden Freibetrag übersteigt.

Andere Personen machen derzeit keine Ansprüche auf die gepfändeten Forderungen
geltend. Die Pfändung künftiger Forderungen ist vorgemerkt. Eigene vorrangige
Ansprüche unsererseits bestehen nicht. Bestehende Pfändungen: Ja

  • Wir haben eine Pfändung von Finanzamt Hannover-Süd, ausgestellt am           ← bullet, pink (BO)
    22.06.2026, für Glas-und Gebäudekosmetik Rosenthal erhalten. Der
    Pfändungsbetrag beträgt 5.721,02 EUR.

Verpflichtungen aus der Nutzung von Debitkarten durch den Schuldner können
anfallen, deren genaue Höhe erst zu einem späteren Zeitpunkt festgestellt werden
kann. Insofern behalten wir uns unsere Pfand- und Aufrechnungsrechte vor.

Wir werden eine Überweisung des pfändbaren Guthabens veranlassen, sobald und
soweit dies rechtlich zulässig ist. Falls die Pfändungsforderung derzeit nicht
oder nicht vollständig befriedigt werden kann, werden wir auf die Angelegenheit
zurückkommen, sobald ein entsprechendes Guthaben verfügbar ist.

Mit freundlichen Grüßen,

Finom Payments B.V.
```

**Supporting-panel sample values:**
- Account: business "GGK-Gebäudereinigung GmbH", UUID `11111111-1111-1111-1111-111111111111`, matched by "IBAN".
- Balance: available **€0,00**; held under seizure **€5.721,02**; seizable **€0,00**.
- Seizure check: **T2**, "1 ongoing (Processing)"; one competing seizure —
  `2614/239/24045 - VO 05 - 728/26 F`, "Processing · 22.06.2026".
- Parsed fields (key/value): case_references, creditor_name, creditor_address,
  debtor_name, date_received (2026-07-09), seizure_amount (1.092,75).

## 6. Screen 2 — Settings

Three stacked config cards + a save bar:
- **LLM:** Provider select (openai / anthropic), Model text input (placeholder
  shows the default per provider), API key (password; shows a masked "— set
  (leave blank to keep)" placeholder when stored; an inline "Clear stored key"
  checkbox), and a **"Test connection"** row that shows ✓/✗ + detail.
- **Back-Office (Finom):** Base URL, INTTOKEN (password, same masked/clear
  pattern), Test connection.
- **Jira:** Base URL + Email (two columns), API token (password, masked/clear),
  Default JQL (with a hint about bounded queries), Test connection.
- **Footer:** primary **"Save changes"** + a small status message ("Saved." /
  "Nothing changed.").
- Loading state (spinner + "Loading settings…") and a top error banner if load fails.

## 7. States to show in the mockups
- Home **empty** (input card only, no results yet).
- Home **with results, T2** (using the sample above) — the hero letter + panels.
- (Nice to have) Home **T1** variant (green badge, no competing-seizure list,
  no bullet block).
- Settings populated.
- A loading spinner and an error/warning banner example.

## 8. What to deliver
1. A **tokens + core-components sheet** (colors, type scale, spacing, buttons,
   badges, banners, inputs).
2. **Home — empty** and **Home — results (T2)** screens.
3. **Settings** screen.
4. Keep it implementable as **React + CSS variables** (no framework). Favor a
   refined **dark** theme; a light variant is a bonus.
