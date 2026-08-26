/**
 * Module Overview
 * ---------------
 * Purpose: React application entrypoint for the Wootify manager frontend.
 * Documentation Standard: module/class/public-method comments.
 */

import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './app/App.jsx';
import './shared/styles/index.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);


