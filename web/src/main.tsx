import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from '@/App';
import '@/index.css';

const container = document.getElementById('root');
if (!container) {
  // Fail loudly rather than silently rendering nothing. An unmounted root with
  // no message is exactly the blank screen B5 describes.
  throw new Error('Root element #root not found in index.html');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
