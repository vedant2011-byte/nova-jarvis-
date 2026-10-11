import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { probeBootHealth } from '../bootHealth'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('startup success', () => {
  it('api() returns parsed JSON on 2xx', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ state: {} })))
    const r = await api<{ state: object }>('/api/status')
    expect(r.state).toEqual({})
  })

  it('probeBootHealth reports ok when the backend answers', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ state: {} })))
    const h = await probeBootHealth(api)
    expect(h.ok).toBe(true)
    expect(h.detail).toBe('backend reachable')
  })
})

describe('API failure', () => {
  it('api() throws on a 500 response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({}, 500)))
    await expect(api('/api/status')).rejects.toThrow('500')
  })

  it('probeBootHealth reports an honest unreachable detail on 500', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({}, 500)))
    const h = await probeBootHealth(api)
    expect(h.ok).toBe(false)
    expect(h.detail).toContain('backend unreachable')
    expect(h.detail).toContain('500')
  })

  it('probeBootHealth reports unreachable when fetch rejects (network down)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    }))
    const h = await probeBootHealth(api)
    expect(h.ok).toBe(false)
    expect(h.detail).toContain('Failed to fetch')
  })
})

describe('timeout handling', () => {
  it('api() aborts a hung request after timeoutMs', async () => {
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(init.signal?.reason ?? new DOMException('aborted', 'AbortError')))
      })))
    await expect(api('/api/status', undefined, 50)).rejects.toMatchObject({
      name: 'TimeoutError',
    })
  })

  it('probeBootHealth distinguishes a probe timeout from a hard failure', async () => {
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(init.signal?.reason ?? new DOMException('aborted', 'AbortError')))
      })))
    const h = await probeBootHealth(api)
    expect(h.ok).toBe(false)
    expect(h.detail).toContain('timed out')
  }, 10000) // the real BOOT_PROBE_TIMEOUT_MS (6s) must elapse for this test

  it('api() does not abort requests that finish in time', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ok: true })))
    const r = await api<{ ok: boolean }>('/api/status', undefined, 5000)
    expect(r.ok).toBe(true)
  })
})
