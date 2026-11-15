const isStreamlit = window.parent !== window && window.location.pathname.includes('/component/')

function connect() {
  const origin = window.location.origin
  const pending = new Map()
  let receiveConfig
  const config = new Promise(resolve => { receiveConfig = resolve })
  const send = (type, payload) => window.parent.postMessage({
    isStreamlitMessage: true, type, ...payload,
  }, origin)

  let frameHeight
  function resize() {
    let height = window.screen.availHeight
    try { height = window.parent.innerHeight } catch { /* Cross-origin frame. */ }
    if (height === frameHeight) return
    frameHeight = height
    send('streamlit:setFrameHeight', { height })
  }

  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== origin || event.data?.type !== 'streamlit:render') return
    const { config: settings, response } = event.data.args
    if (settings) receiveConfig(settings)
    const task = response && pending.get(response.id)
    if (task) {
      pending.delete(response.id)
      if (response.error) task.reject(new Error(response.error))
      else task.resolve(response.result)
    }
    resize()
  })
  window.addEventListener('resize', resize)
  send('streamlit:componentReady', { apiVersion: 1 })
  resize()

  return async (path, body, signal) => {
    if (path === 'config') return config
    await config
    if (signal?.aborted) throw signal.reason
    return new Promise((resolve, reject) => {
      const id = window.crypto.randomUUID()
      pending.set(id, { resolve, reject })
      send('streamlit:setComponentValue', { dataType: 'json', value: { id, path, body } })
    })
  }
}

export const streamlitRequest = isStreamlit ? connect() : null
