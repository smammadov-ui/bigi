// Tiny fetch wrapper around the bigi backend.
//
// Every call returns parsed JSON. On a non-2xx response it throws an Error
// whose message is the backend's FastAPI `detail` field ({ "detail": "..." }),
// falling back to a generic HTTP message when no detail is present.

async function request(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }

  const res = await fetch(url, opts);

  // Try to parse JSON regardless of status so we can read `detail` on errors.
  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    let detail;
    if (data && typeof data === 'object' && 'detail' in data) {
      detail = data.detail;
      if (Array.isArray(detail)) {
        // FastAPI validation errors come back as a list of objects.
        detail = detail
          .map((d) => (d && d.msg ? d.msg : JSON.stringify(d)))
          .join('; ');
      } else if (detail && typeof detail === 'object') {
        detail = JSON.stringify(detail);
      }
    }
    const message = detail || `HTTP ${res.status} ${res.statusText}`;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }

  return data;
}

// ---- Settings ----
export function getSettings() {
  return request('GET', '/api/settings');
}

export function putSettings(patch) {
  return request('PUT', '/api/settings', patch);
}

export function testBO() {
  return request('POST', '/api/settings/test/bo');
}

export function testJira() {
  return request('POST', '/api/settings/test/jira');
}

export function testLLM() {
  return request('POST', '/api/settings/test/llm');
}

// ---- Declaration pipeline ----
// company_uuid is an optional operator override after candidate selection /
// manual entry; omitted on the first pass.
export function postDeclaration(raw_text, company_uuid, no_match) {
  const body = { raw_text };
  if (company_uuid) body.company_uuid = company_uuid;
  if (no_match) body.no_match = true;
  return request('POST', '/api/declaration', body);
}

// ---- Jira ----
export function jiraFetch(issue_key) {
  return request('POST', '/api/jira/fetch', { issue_key });
}

export function jiraSearch(jql) {
  return request('GET', `/api/jira/search${jql ? `?jql=${encodeURIComponent(jql)}` : ''}`);
}
