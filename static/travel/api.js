export class ApiError extends Error {
  constructor(status, detail, code) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

export async function liveRequest(path, {method = 'GET', body, key} = {}) {
  const form = body instanceof FormData;
  const headers = new Headers();
  if (body !== undefined && !form) headers.set('Content-Type', 'application/json');
  if (key) headers.set('Idempotency-Key', key);
  const response = await fetch(path, {method, headers, body: body === undefined || form ? body : JSON.stringify(body), cache: 'no-store'});
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    throw new ApiError(response.status, (typeof detail === 'string' ? detail : detail?.message) || data?.message || `请求失败（${response.status}）`, data?.code || detail?.code);
  }
  if (data === null) throw new ApiError(response.status, '服务返回了无法读取的数据。');
  return data;
}

export const runPath = id => `/api/runs/${encodeURIComponent(id)}`;
export function liveEvents(id, after, receive, connection) {
  const source = new EventSource(`${runPath(id)}/events?after=${after}`);
  source.onopen = () => connection('open');
  source.onerror = () => connection('retry');
  source.onmessage = ({data, lastEventId}) => {
    let event;
    try { event = JSON.parse(data); } catch { return; }
    receive(lastEventId, event);
  };
  return () => source.close();
}

export function requestId() {
  return globalThis.crypto.randomUUID?.() ||
    Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
}
