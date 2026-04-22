import React, { useState, useEffect } from 'react';
import './App.css';
import MainContent from './components/MainContent';
import ChatAssistant from './components/ChatAssistant';
import ErrorBoundary from './components/ErrorBoundary';

export type ObservabilityPage = 'metrics' | 'logs' | 'traces';

interface ContextItem {
  description: string;
  value: string;
}

const App: React.FC = () => {
  const [selectedPage, setSelectedPage] = useState<ObservabilityPage>('metrics');
  const [pageContext, setPageContext] = useState<ContextItem[]>([]);
  const [triggerQuery, setTriggerQuery] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(false);

  // Store separate queries for each page
  const [pageQueries, setPageQueries] = useState<Record<ObservabilityPage, string>>({
    metrics: '',
    logs: '',
    traces: ''
  });

  // Handle PromQL query execution from ChatAssistant
  const handleExecutePromQLQuery = (query: string) => {
    // Navigate to metrics page
    setSelectedPage('metrics');

    // Store the query for metrics page
    setPageQueries(prev => ({
      ...prev,
      metrics: query
    }));

    // Update URL to reflect the change
    const urlParams = new URLSearchParams(window.location.search);
    urlParams.set('app', 'metrics');
    urlParams.set('query', encodeURIComponent(query));
    const newUrl = `${window.location.pathname}?${urlParams.toString()}`;
    window.history.replaceState({}, '', newUrl);

    // Set the query to be executed
    setTriggerQuery(query);
  };

  // Handle PPL query execution from ChatAssistant
  const handleExecutePPLQuery = (query: string) => {
    // Navigate to logs page
    setSelectedPage('logs');

    // Store the query for logs page
    setPageQueries(prev => ({
      ...prev,
      logs: query
    }));

    // Update URL to reflect the change
    const urlParams = new URLSearchParams(window.location.search);
    urlParams.set('app', 'logs');
    urlParams.set('query', encodeURIComponent(query));
    const newUrl = `${window.location.pathname}?${urlParams.toString()}`;
    window.history.replaceState({}, '', newUrl);

    // Set the query to be executed
    setTriggerQuery(query);
  };

  // Clear trigger after it's been processed
  const clearTriggerQuery = () => {
    setTriggerQuery(null);
  };

  // Handle query updates from MainContent
  const handleQueryUpdate = React.useCallback((page: ObservabilityPage, query: string) => {
    setPageQueries(prev => ({
      ...prev,
      [page]: query
    }));
  }, []); // No dependencies needed since setPageQueries is stable

  // Read URL parameters on mount
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const appParam = urlParams.get('app');
    const queryParam = urlParams.get('query');

    // Set page from URL parameter or default to metrics
    if (appParam && ['metrics', 'logs', 'traces'].includes(appParam)) {
      setSelectedPage(appParam as ObservabilityPage);
    } else {
      // Default to metrics and update URL
      setSelectedPage('metrics');
      urlParams.set('app', 'metrics');
      const newUrl = `${window.location.pathname}?${urlParams.toString()}`;
      window.history.replaceState({}, '', newUrl);
    }

    // Set initial query from URL parameter for the current page
    if (queryParam) {
      const decodedQuery = decodeURIComponent(queryParam);
      const currentPage = (appParam && ['metrics', 'logs', 'traces'].includes(appParam))
        ? appParam as ObservabilityPage
        : 'metrics';

      setPageQueries(prev => ({
        ...prev,
        [currentPage]: decodedQuery
      }));
    }
  }, []);

  // Update URL when page changes
  const handlePageChange = (page: ObservabilityPage) => {
    setSelectedPage(page);

    // Update URL parameters
    const urlParams = new URLSearchParams(window.location.search);
    urlParams.set('app', page);

    // Set the query parameter to the stored query for this page
    const storedQuery = pageQueries[page];
    if (storedQuery) {
      urlParams.set('query', encodeURIComponent(storedQuery));
    } else {
      urlParams.delete('query');
    }

    const newUrl = `${window.location.pathname}?${urlParams.toString()}`;
    window.history.replaceState({}, '', newUrl);
  };

  return (
    <div className="app">
      <div className={`left-sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
          <h2>{sidebarCollapsed ? '' : 'ExampleOps'}</h2>
          <button
            className="sidebar-collapse-btn"
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points={sidebarCollapsed ? "9 18 15 12 9 6" : "15 18 9 12 15 6"} />
            </svg>
          </button>
        </div>
        <nav className="sidebar-nav">
          <button
            className={`nav-item ${selectedPage === 'metrics' ? 'active' : ''}`}
            onClick={() => handlePageChange('metrics')}
            title="Metrics"
          >
            <span className="nav-icon">
              <svg viewBox="0 0 24 24"><path d="M3 3v18h18" /><path d="M18 17V9" /><path d="M13 17V5" /><path d="M8 17v-3" /></svg>
            </span>
            {!sidebarCollapsed && 'Metrics'}
            {!sidebarCollapsed && <span className="nav-status-dot connected"></span>}
          </button>
          <button
            className={`nav-item ${selectedPage === 'logs' ? 'active' : ''}`}
            onClick={() => handlePageChange('logs')}
            title="Logs"
          >
            <span className="nav-icon">
              <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /><polyline points="10 9 9 9 8 9" /></svg>
            </span>
            {!sidebarCollapsed && 'Logs'}
            {!sidebarCollapsed && <span className="nav-status-dot connected"></span>}
          </button>
          <button
            className="nav-item disabled"
            disabled
            title="Coming soon"
          >
            <span className="nav-icon">
              <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
            </span>
            {!sidebarCollapsed && 'Traces'}
          </button>
        </nav>
        <div className="nav-separator"></div>
        <div className="sidebar-footer">
          <div className="sidebar-footer-inner">
            <span>by</span>
            <a
              href="https://github.com/kylehounslow"
              target="_blank"
              rel="noopener noreferrer"
              className="github-credit"
            >
              <svg className="github-logo" viewBox="0 0 16 16" width="12" height="12">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
              </svg>
              kylehounslow
            </a>
            <p>and</p>
            <a
              href="https://github.com/devbyaryanvala"
              target="_blank"
              rel="noopener noreferrer"
              className="github-credit"
            >
              <svg className="github-logo" viewBox="0 0 16 16" width="12" height="12">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
              </svg>
              devbyaryanvala
            </a>
          </div>
        </div>
      </div>
      <MainContent
        selectedPage={selectedPage}
        initialQuery={pageQueries[selectedPage]}
        triggerQuery={triggerQuery}
        onContextChange={setPageContext}
        onQueryTriggered={clearTriggerQuery}
        onQueryUpdate={handleQueryUpdate}
      />
      <ErrorBoundary>
        <div className={`chat-panel-wrapper ${chatCollapsed ? 'collapsed' : ''}`}>
          <ChatAssistant
            pageContext={pageContext}
            onExecutePromQLQuery={handleExecutePromQLQuery}
            onExecutePPLQuery={handleExecutePPLQuery}
            onCollapse={() => setChatCollapsed(true)}
          />
        </div>
      </ErrorBoundary>
      {chatCollapsed && (
        <button
          className="chat-open-btn"
          onClick={() => setChatCollapsed(false)}
          title="Open HolmesGPT Chat"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
      )}
    </div>
  );
};

export default App;
