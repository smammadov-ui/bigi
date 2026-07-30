# templates_de.md — German declaration + email templates (from "Copy of Seizures.docx")

Placeholders (green = Back Office, blue = Jira): `[Case number]`, `[Creditor]`, `[creditor Address]`, `[debtor name]`, `[Delivered on]`, `[Total seizure amount]`, `[Seized amount]`, `[Comment]`, `[case references]`, and the flags `[Kundenbeziehung besteht: Ja|Nein|N/A]`, `[Bestehende Pfändungen: Nein|Ja|N/A]`.

**Field mapping corrections (from this doc):**
- **Gläubiger = `[Creditor], [creditor Address]`** (creditor name **+ address** — supersedes the earlier "name only").
- **Pfändungsbetrag = `[Total seizure amount]`** (the requested total).
- **"…in Höhe von `[Seized amount]` EUR" = the seizable/available amount** → this is the **"ceased amount" (Q8)**. (Distinct from Pfändungsbetrag.)
- **Zustellungsdatum bei uns = `[Delivered on]`**.
- `[Kundenbeziehung besteht]` = `Ja` when the account exists (S1/S2), `Nein` when closed / in onboarding (S3).
- `[Bestehende Pfändungen]` = `Nein` (S1), `Ja` + `[Comment]` (S2), `N/A` (S3).

---

## T1 — Scenario 1: Normal TPD (match, no open alerts, no processing seizures)
```
Drittschuldnererklärung gemäß § 840 ZPO zum Aktenzeichen [Case number]
Gläubiger: [Creditor], [creditor Address]
Schuldner: [debtor name]
Zustellungsdatum bei uns: [Delivered on]
Pfändungsbetrag: [Total seizure amount] EUR

Sehr geehrte Damen und Herren,
unter Bezugnahme auf den uns zugestellten Pfändungs- und Überweisungsbeschluss geben wir hiermit fristgerecht die folgende Erklärung als Drittschuldner gemäß § 840 der Zivilprozessordnung (ZPO) ab:
Der Schuldner unterhält eine Geschäftsbeziehung zu uns. Eine Forderung des Schuldners gegen uns besteht grundsätzlich. [Kundenbeziehung besteht: Ja].
Die gepfändete Forderung besteht derzeit in Höhe von [Seized amount] EUR.
Die gepfändete Forderung betrifft Guthaben auf Konten, die keine Pfändungsschutzkonten im Sinne des § 850k ZPO sind. Derzeit ist kein pfändbares Guthaben verfügbar, das den geltenden Freibetrag übersteigt.
Andere Personen machen derzeit keine Ansprüche auf die gepfändeten Forderungen geltend. Die Pfändung künftiger Forderungen ist vorgemerkt. Eigene vorrangige Ansprüche unsererseits bestehen nicht. [Bestehende Pfändungen: Nein].
Verpflichtungen aus der Nutzung von Debitkarten durch den Schuldner können anfallen, deren genaue Höhe erst zu einem späteren Zeitpunkt festgestellt werden kann. Insofern behalten wir uns unsere Pfand- und Aufrechnungsrechte vor.
Wir werden eine Überweisung des pfändbaren Guthabens veranlassen, sobald und soweit dies rechtlich zulässig ist. Falls die Pfändungsforderung derzeit nicht oder nicht vollständig befriedigt werden kann, werden wir auf die Angelegenheit zurückkommen, sobald ein entsprechendes Guthaben verfügbar ist.
Mit freundlichen Grüßen,
Finom Payments B.V.
```

## T2 — Scenario 2: TPD with previous Processing seizures (no open alerts)
Same as T1, except: `[Kundenbeziehung besteht: Ja]`, "in Höhe von **0.00** EUR", and:
```
…Eigene vorrangige Ansprüche unsererseits bestehen nicht. [Bestehende Pfändungen: Ja]
[Comment]
```
(`[Comment]` = the Processing seizures' comments — Jira links stripped, translated to German, chronological.)

## T3 — MNL20 (Seizure mechanism) — no customer letter
**New-style civil** → proceed with the seizure feature (Scenarios 1–6). **Criminal** → stay in MNL20, handle **confidentially** (the feature notifies the customer = "tipping off"). **Old-style civil** → manual wallet transfer (main → seizure → external) + four-eyes. No customer letter is generated from this branch.

## T4 — Open alert MNL-21-FP (Insolvency) → NO seizure creation; send email
```
Sehr geehrte Damen und Herren,
bitte beachten Sie, dass wir diese Pfändung aufgrund eines laufenden Insolvenzverfahrens über das Konto derzeit nicht bearbeiten können. Wir haben die Forderung vorgemerkt und werden die Bearbeitung Ihres Ersuchens priorisieren, sobald die Angelegenheit geklärt ist.
Vielen Dank für Ihr Verständnis.
```

## T5 — MNL22 (Information request / RFI) — no seizure
**Not a seizure.** The authority requests data (IP logs, account balance, statements). Gather it from the back office and provide it to the authority — no seizure is created and no §840 letter is sent.

## T6 — Scenario 3: Account closed before the ticket / verification in progress
Trigger: Payment account status `Account Closed` (closed **before** ticket received) or `Application in progress`. *(If the account closed **after** the ticket → needs manual handling.)*
Same letter as T1, except: `[Kundenbeziehung besteht: Nein]`, "in Höhe von **0.00** EUR", `[Bestehende Pfändungen: N/A]`.

## T7 — Scenario 4: Cannot find user, **no IBAN provided** (Company: address mismatch; Freelancer: address AND DOB mismatch) → ask for IBAN
```
Sehr geehrte Damen und Herren,
wir hoffen, dass es Ihnen gut geht.
Leider konnten wir die betreffende Person anhand der uns vorliegenden Informationen nicht in unserem System finden. Um den entsprechenden Datensatz eindeutig zu identifizieren und Ihr Anliegen weiterbearbeiten zu können, bitten wir Sie höflich, uns die zugehörige IBAN mitzuteilen.
Sobald wir diese Information erhalten haben, werden wir den Vorgang umgehend weiterverfolgen.
Vielen Dank für Ihre Unterstützung. Wir freuen uns auf Ihre Rückmeldung.
Mit freundlichen Grüßen
```

## T8 — Scenario 4 variant: **IBAN provided but not matching** → ask for correct IBAN
```
Sehr geehrte Damen und Herren,
wir hoffen, dass es Ihnen gut geht.
Leider konnten wir die betreffende Person anhand der uns vorliegenden Informationen nicht in unserem System identifizieren. Zudem haben wir festgestellt, dass die von Ihnen angegebene IBAN in unserem System nicht erfasst ist bzw. nicht zugeordnet werden kann.
Um den entsprechenden Datensatz eindeutig zu identifizieren und Ihr Anliegen weiterbearbeiten zu können, bitten wir Sie daher höflich, uns die korrekte IBAN mitzuteilen.
Sobald wir diese Information erhalten haben, werden wir den Vorgang umgehend weiterverfolgen.
Vielen Dank für Ihre Unterstützung. Wir freuen uns auf Ihre Rückmeldung.
Mit freundlichen Grüßen
```

## T9 — Scenario 5: Seizure against an individual, but the account is a Company (not a Freelancer)
```
Sehr geehrte Damen und Herren,
wir hoffen, es geht Ihnen gut.
Die vorliegende Pfändungsverfügung richtet sich gegen eine Privatperson und nicht gegen ein Unternehmen. Da es sich bei dem betroffenen Konto um ein Geschäftskonto handelt, können wir die Pfändung nicht bearbeiten.
Zu Ihrer Orientierung haben wir das von Ihnen erhaltene Dokument beigefügt, um eindeutig zu kennzeichnen, auf welchen Vorgang wir uns beziehen.
Mit freundlichen Grüßen
```
(Attach the received seizure document.)

## T10 — Scenario 6A: Scheduled to close, balance + a previous Processing seizure / 0.00 balance → uses `[case references]`
```
Sehr geehrte Damen und Herren,

wir hoffen, dass es Ihnen gut geht.
Leider können wir die eingegangene Pfändung mit der Protokollnummer [case references] nicht weiter bearbeiten, da sich das betreffende Konto bereits vor dem Datum des Eingangs Ihres Pfändungsersuchens im Prozess der Kontoschließung befand.
Zu Ihrer Information fügen wir das von Ihnen erhaltene Dokument bei, um eindeutig zu belegen, auf welchen Vorgang wir uns beziehen.
Für weitere Fragen stehen wir Ihnen selbstverständlich gerne zur Verfügung.

Mit freundlichen Grüßen
```

## T11 — Scenario 6B: Scheduled to close, balance, NO Processing seizure → transfer only `[Seized amount]`, uses `[Case number]`
```
Sehr geehrte Damen und Herren,
wir hoffen, es geht Ihnen gut.
Leider können wir Ihren Pfändungsantrag mit der Referenznummer [Case number] nicht bearbeiten, da das betreffende Konto bereits vor Eingang Ihres Antrags geschlossen wurde. Wir können Ihnen daher nur den Restbetrag in Höhe von [Seized amount] überweisen.
Zur Information fügen wir das von Ihnen bereitgestellte Dokument bei, um den betreffenden Fall eindeutig zu verdeutlichen.
Bei weiteren Fragen stehen wir Ihnen gerne zur Verfügung.
Mit freundlichen Grüßen
```
