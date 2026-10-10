import React, { useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import { Hud } from './hud/Hud'
import './styles/global.css'
import './styles/hud.css'
import './styles/settings.css'

function Root() {
  const [intensity, setIntensity] = React.useState(1.0)

  useEffect(() => {
    fetch('/api/settings')
      .then(r => r.json())
      .then((d: { settings: { appearance?: { animation_intensity?: number; brightness?: number } } }) => {
        setIntensity(d.settings?.appearance?.animation_intensity ?? 1.0)
      })
      .catch(() => undefined)
    const onStorage = () => undefined
    window.addEventListener('settings-updated', onStorage)
    return () => window.removeEventListener('settings-updated', onStorage)
  }, [])

  // poll for intensity changes (settings modal may not be mounted here)
  useEffect(() => {
    const t = setInterval(() => {
      fetch('/api/settings')
        .then(r => r.json())
        .then((d: { settings: { appearance?: { animation_intensity?: number } } }) => {
          setIntensity(d.settings?.appearance?.animation_intensity ?? 1.0)
        })
        .catch(() => undefined)
    }, 5000)
    return () => clearInterval(t)
  }, [])

  return <Hud intensity={intensity} />
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)
