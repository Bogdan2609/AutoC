import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
try { document.documentElement.classList.toggle('dark', localStorage.getItem('autocodex-admin-theme') === 'dark') } catch { /* storage disabled */ }
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)
