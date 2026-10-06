import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router';
import App from '@/App';
import '@/index.css';

const container = document.getElementById('root');
if (!container) {
  // Fail loudly rather than silently rendering nothing. An unmounted root with
  // no message is exactly the blank screen B5 describes.
  throw new Error('Root element #root not found in index.html');
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Analytics change every five minutes at most (the rollup cadence, ADR-0016).
      staleTime: 60_000,
      // One retry, not three: a 4xx will not fix itself, and a panel should reach its
      // error state quickly rather than spin (F9.AC18).
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
