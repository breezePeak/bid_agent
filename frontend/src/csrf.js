const CSRF_COOKIE = 'bid_agent_csrf'

export function csrfToken() {
  const prefix = `${CSRF_COOKIE}=`
  const item = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : ''
}

export function installCsrfFetch() {
  const originalFetch = window.fetch.bind(window)
  window.fetch = (input, init = {}) => {
    const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase()
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.href)
    if (url.origin !== window.location.origin || ['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      return originalFetch(input, init)
    }
    const token = csrfToken()
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined))
    if (token) headers.set('X-CSRF-Token', token)
    return originalFetch(input, { ...init, headers })
  }
}
