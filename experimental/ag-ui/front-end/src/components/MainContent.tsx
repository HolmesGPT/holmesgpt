import React, { useState } from 'react';
import GraphVisualization from './GraphVisualization';
import LogsVisualization from './LogsVisualization';
type ObservabilityPage = 'metrics' | 'logs' | 'traces';

interface QueryResult {
  id: string;
  query: string;
  timestamp: Date;
  data?: any;
  error?: string;
  errorDetails?: any;
}

interface ContextItem {
  description: string;
  value: string;
}

interface MainContentProps {
  selectedPage: ObservabilityPage;
  initialQuery?: string;
  triggerQuery?: string | null;
  onContextChange?: (context: ContextItem[]) => void;
  onQueryTriggered?: () => void;
  onQueryUpdate?: (page: ObservabilityPage, query: string) => void;
}

const MainContent: React.FC<MainContentProps> = ({
  selectedPage,
  initialQuery = '',
  triggerQuery,
  onContextChange,
  onQueryTriggered,
  onQueryUpdate
}) => {
  const [query, setQuery] = useState(initialQuery);
  const [isExecuting, setIsExecuting] = useState(false);

  // Track current query execution to prevent race conditions
  const currentQueryRef = React.useRef<string>('');
  const abortControllerRef = React.useRef<AbortController | null>(null);

  // Store separate results for each page
  const [pageResults, setPageResults] = useState<Record<ObservabilityPage, QueryResult | null>>({
    metrics: null,
    logs: null,
    traces: null
  });

  // Get current page's result
  const currentResult = pageResults[selectedPage];
  const [prometheusStatus, setPrometheusStatus] = useState<'checking' | 'connected' | 'disconnected'>('checking');
  const [prometheusUrl] = useState(process.env.REACT_APP_PROMETHEUS_URL || 'http://localhost:9090');
  const [opensearchStatus, setOpensearchStatus] = useState<'checking' | 'connected' | 'disconnected'>('checking');
  const [opensearchUrl] = useState(process.env.REACT_APP_OPENSEARCH_URL || 'http://localhost:9200');
  const [opensearchUser] = useState(process.env.REACT_APP_OPENSEARCH_USER);
  const [opensearchPassword] = useState(process.env.REACT_APP_OPENSEARCH_PASSWORD);

  // Indices discovery state
  const [availableIndices, setAvailableIndices] = useState<string[]>([]);

  const [loadingIndices, setLoadingIndices] = useState(false);

  // Metrics discovery state - three-box interface
  const [availableMetrics, setAvailableMetrics] = useState<string[]>([]);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [availableLabels, setAvailableLabels] = useState<string[]>([]);
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const [availableLabelValues, setAvailableLabelValues] = useState<string[]>([]);
  const [loadingMetrics, setLoadingMetrics] = useState(false);
  const [loadingLabels, setLoadingLabels] = useState(false);
  const [loadingLabelValues, setLoadingLabelValues] = useState(false);
  const [showExplorer, setShowExplorer] = useState(false);
  const [showIndicesExplorer, setShowIndicesExplorer] = useState(false);
  const [isMaximized, setIsMaximized] = useState(false);

  // Helper function to create OpenSearch auth headers
  const getOpensearchHeaders = React.useCallback(() => {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    if (opensearchUser && opensearchPassword) {
      const credentials = btoa(`${opensearchUser}:${opensearchPassword}`);
      headers['Authorization'] = `Basic ${credentials}`;
    }

    return headers;
  }, [opensearchUser, opensearchPassword]);

  // Fetch indices count automatically when connected
  const fetchIndicesCount = React.useCallback(async () => {
    if (selectedPage !== 'logs' || opensearchStatus !== 'connected') return;

    try {
      const response = await fetch(`${opensearchUrl}/_cat/indices?format=json&h=index`, {
        method: 'GET',
        headers: getOpensearchHeaders(),
        signal: AbortSignal.timeout(10000), // 10 second timeout
      });

      if (response.ok) {
        const indices = await response.json();
        const indexNames = indices
          .map((idx: any) => idx.index)
          .filter((name: string) => !name.startsWith('.')) // Filter out system indices
          .sort();

        setAvailableIndices(indexNames);
      } else {
        console.error('Failed to fetch indices count:', response.status, response.statusText);
        setAvailableIndices([]);
      }
    } catch (error) {
      console.error('Error fetching indices count:', error);
      setAvailableIndices([]);
    }
  }, [opensearchUrl, selectedPage, opensearchStatus, getOpensearchHeaders]);

  // Fetch metrics count automatically when connected
  const fetchMetricsCount = React.useCallback(async () => {
    if (selectedPage !== 'metrics' || prometheusStatus !== 'connected') return;

    try {
      const response = await fetch(`${prometheusUrl}/api/v1/label/__name__/values`, {
        method: 'GET',
        signal: AbortSignal.timeout(10000), // 10 second timeout
      });

      if (response.ok) {
        const result = await response.json();
        if (result.status === 'success' && result.data) {
          const metricNames = result.data
            .filter((name: string) => name && !name.startsWith('__')) // Filter out internal metrics
            .sort();

          setAvailableMetrics(metricNames);
        } else {
          console.error('Failed to fetch metrics: Invalid response format');
          setAvailableMetrics([]);
        }
      } else {
        console.error('Failed to fetch metrics:', response.status, response.statusText);
        setAvailableMetrics([]);
      }
    } catch (error) {
      console.error('Error fetching metrics:', error);
      setAvailableMetrics([]);
    }
  }, [prometheusUrl, selectedPage, prometheusStatus]);

  // Fetch labels for selected metric
  const fetchLabelsForMetric = React.useCallback(async (metricName: string) => {
    if (!metricName || selectedPage !== 'metrics' || prometheusStatus !== 'connected') return;

    setLoadingLabels(true);
    try {
      const response = await fetch(`${prometheusUrl}/api/v1/series?match[]=${encodeURIComponent(metricName)}&limit=1000`, {
        method: 'GET',
        signal: AbortSignal.timeout(10000),
      });

      if (response.ok) {
        const result = await response.json();
        if (result.status === 'success' && result.data) {
          const labelSet = new Set<string>();
          result.data.forEach((series: any) => {
            Object.keys(series).forEach(label => {
              if (label !== '__name__') {
                labelSet.add(label);
              }
            });
          });

          const labels = Array.from(labelSet).sort();
          setAvailableLabels(labels);
        } else {
          setAvailableLabels([]);
        }
      } else {
        console.error('Failed to fetch labels:', response.status, response.statusText);
        setAvailableLabels([]);
      }
    } catch (error) {
      console.error('Error fetching labels:', error);
      setAvailableLabels([]);
    } finally {
      setLoadingLabels(false);
    }
  }, [prometheusUrl, selectedPage, prometheusStatus]);

  // Fetch label values for selected metric and label
  const fetchLabelValues = React.useCallback(async (metricName: string, labelName: string) => {
    if (!metricName || !labelName || selectedPage !== 'metrics' || prometheusStatus !== 'connected') return;

    setLoadingLabelValues(true);
    try {
      const response = await fetch(`${prometheusUrl}/api/v1/label/${encodeURIComponent(labelName)}/values?match[]=${encodeURIComponent(metricName)}`, {
        method: 'GET',
        signal: AbortSignal.timeout(10000),
      });

      if (response.ok) {
        const result = await response.json();
        if (result.status === 'success' && result.data) {
          const values = result.data.sort();
          setAvailableLabelValues(values);
        } else {
          setAvailableLabelValues([]);
        }
      } else {
        console.error('Failed to fetch label values:', response.status, response.statusText);
        setAvailableLabelValues([]);
      }
    } catch (error) {
      console.error('Error fetching label values:', error);
      setAvailableLabelValues([]);
    } finally {
      setLoadingLabelValues(false);
    }
  }, [prometheusUrl, selectedPage, prometheusStatus]);

  // Handle metric selection
  const handleMetricSelect = React.useCallback((metric: string) => {
    setSelectedMetric(metric);
    setSelectedLabel(null);
    setAvailableLabels([]);
    setAvailableLabelValues([]);
    fetchLabelsForMetric(metric);
  }, [fetchLabelsForMetric]);

  // Handle label selection
  const handleLabelSelect = React.useCallback((label: string) => {
    if (!selectedMetric) return;
    setSelectedLabel(label);
    setAvailableLabelValues([]);
    fetchLabelValues(selectedMetric, label);
  }, [selectedMetric, fetchLabelValues]);

  // Handle label value selection - build query
  const handleLabelValueSelect = React.useCallback((value: string) => {
    if (!selectedMetric || !selectedLabel) return;
    const query = `${selectedMetric}{${selectedLabel}="${value}"}`;
    setQuery(query);
    // Collapse the explorer since the user has completed building their query
    setShowExplorer(false);
  }, [selectedMetric, selectedLabel]);





  const isUpdatingFromParent = React.useRef(false);

  // Retry delay state
  const prometheusRetryTimeoutRef = React.useRef<NodeJS.Timeout | null>(null);
  const opensearchRetryTimeoutRef = React.useRef<NodeJS.Timeout | null>(null);

  // Update context for ChatAssistant
  const updateContext = React.useCallback(() => {
    if (!onContextChange) return;

    const context: ContextItem[] = [];

    // Add current page info
    context.push({
      description: "Current page",
      value: selectedPage
    });

    // Add current query if exists
    if (query.trim()) {
      // If metrics page, assume PromQL. For logs assume PPL
      if (selectedPage === 'metrics') {
        context.push({
          description: "Current PromQL query",
          value: query.trim()
        });
      } else if (selectedPage === 'logs') {
        context.push({
          description: "Current PPL query",
          value: query.trim()
        });
      } else{
      context.push({
        description: `Current ${selectedPage} query`,
        value: query.trim()
      });
      }

    }

    // Add current result info if exists
    if (currentResult) {
      if (currentResult.error) {
        let errorValue = currentResult.error;

        // For logs, include detailed error response in the same entry
        if (selectedPage === 'logs' && currentResult.errorDetails) {
          errorValue += `\n\nDetailed error response: ${JSON.stringify(currentResult.errorDetails, null, 2)}`;
        }

        context.push({
          description: `${selectedPage} query error`,
          value: errorValue
        });

        // Add detailed error response for non-logs pages only
        if (selectedPage !== 'logs' && currentResult.errorDetails) {
          context.push({
            description: `${selectedPage} error response`,
            value: JSON.stringify(currentResult.errorDetails)
          });
        }
      } else if (currentResult.data && selectedPage !== 'logs') {
        // Only add success status for non-logs pages
        context.push({
          description: `${selectedPage} query status`,
          value: "Success - data available for visualization"
        });
      }
    }

    // Add connection status for metrics page only
    if (selectedPage === 'metrics') {
      context.push({
        description: "Prometheus connection status",
        value: `${prometheusStatus} (${prometheusUrl})`
      });
    }

    onContextChange(context);
  }, [selectedPage, query, currentResult, prometheusStatus, prometheusUrl, onContextChange]);

  // Update context whenever relevant state changes
  React.useEffect(() => {
    updateContext();
  }, [updateContext]);

  // Auto-fetch indices count when OpenSearch connection becomes healthy
  React.useEffect(() => {
    if (selectedPage === 'logs') {
      if (opensearchStatus === 'connected' && availableIndices.length === 0) {
        fetchIndicesCount();
      } else if (opensearchStatus === 'disconnected') {
        // Clear indices when connection is lost
        setAvailableIndices([]);
      }
    }
  }, [selectedPage, opensearchStatus, availableIndices.length, fetchIndicesCount]);

  // Auto-fetch metrics when Prometheus connection becomes healthy
  React.useEffect(() => {
    if (selectedPage === 'metrics') {
      if (prometheusStatus === 'connected' && availableMetrics.length === 0) {
        fetchMetricsCount();
      } else if (prometheusStatus === 'disconnected') {
        // Clear all metrics data when connection is lost
        setAvailableMetrics([]);
        setSelectedMetric(null);
        setAvailableLabels([]);
        setSelectedLabel(null);
        setAvailableLabelValues([]);
      }
    }
  }, [selectedPage, prometheusStatus, availableMetrics.length, fetchMetricsCount]);

  // Check Prometheus connection status
  const checkPrometheusConnection = React.useCallback(async (isRetry = false, force = false) => {
    if (selectedPage !== 'metrics') {
      setPrometheusStatus('connected'); // Don't check for non-metrics pages
      return;
    }

    // Prevent multiple concurrent checks (but allow retries and force checks)
    if (prometheusStatus === 'checking' && !isRetry && !force) {
      console.log('Prometheus check already in progress, skipping');
      return;
    }

    // Clear any existing retry timeout
    if (prometheusRetryTimeoutRef.current) {
      clearTimeout(prometheusRetryTimeoutRef.current);
      prometheusRetryTimeoutRef.current = null;
    }

    // Add delay for retries to prevent overwhelming the server
    if (isRetry) {
      await new Promise(resolve => setTimeout(resolve, 2000)); // 2 second delay for retries
    }

    // Safety timeout to prevent getting stuck in 'checking' state
    let safetyTimeout: NodeJS.Timeout | null = null;

    try {
      console.log('Starting Prometheus connection check...', { prometheusUrl, isRetry, currentStatus: prometheusStatus });
      setPrometheusStatus('checking');
      console.log('Set status to checking, now fetching...', prometheusUrl);

      safetyTimeout = setTimeout(() => {
        console.warn('Prometheus check taking too long, forcing disconnected state');
        setPrometheusStatus('disconnected');
      }, 12000); // 12 second safety timeout

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 8000); // 8 second timeout

      // Try a simpler endpoint first
      console.log('About to fetch from:', `${prometheusUrl}/api/v1/query?query=up`);
      const response = await fetch(`${prometheusUrl}/api/v1/query?query=up`, {
        method: 'GET',
        signal: controller.signal
      });

      console.log('Fetch completed, clearing timeouts...');
      clearTimeout(timeoutId);
      if (safetyTimeout) clearTimeout(safetyTimeout);

      console.log('Prometheus response received:', { status: response.status, ok: response.ok, url: response.url });
      if (response.ok) {
        console.log('Setting Prometheus status to connected');
        setPrometheusStatus('connected');
      } else {
        console.log('Setting Prometheus status to disconnected');
        setPrometheusStatus('disconnected');
      }
    } catch (error: any) {
      console.warn('Prometheus connection check failed:', error);

      // Clear safety timeout if it exists
      if (safetyTimeout) clearTimeout(safetyTimeout);

      // Handle specific error types
      if (error.name === 'AbortError') {
        console.warn('Prometheus connection check timed out');
      }

      setPrometheusStatus('disconnected');

      // Auto-retry after 10 seconds if not a manual retry
      if (!isRetry) {
        prometheusRetryTimeoutRef.current = setTimeout(() => {
          console.log('Auto-retrying Prometheus connection...');
          checkPrometheusConnection(true);
        }, 10000);
      }
    }
  }, [prometheusUrl, selectedPage]);

  // Check OpenSearch connection status
  const checkOpensearchConnection = React.useCallback(async (isRetry = false) => {
    if (selectedPage !== 'logs') {
      setOpensearchStatus('connected'); // Don't check for non-logs pages
      return;
    }

    // Clear any existing retry timeout
    if (opensearchRetryTimeoutRef.current) {
      clearTimeout(opensearchRetryTimeoutRef.current);
      opensearchRetryTimeoutRef.current = null;
    }

    // Add delay for retries to prevent overwhelming the server
    if (isRetry) {
      await new Promise(resolve => setTimeout(resolve, 2000)); // 2 second delay for retries
    }

    try {
      setOpensearchStatus('checking');
      // Try basic cluster info first, then health endpoint
      let response = await fetch(`${opensearchUrl}/`, {
        method: 'GET',
        headers: getOpensearchHeaders(),
        signal: AbortSignal.timeout(5000), // 5 second timeout
      });

      // If root endpoint fails, try cluster health
      if (!response.ok) {
        response = await fetch(`${opensearchUrl}/_cluster/health`, {
          method: 'GET',
          headers: getOpensearchHeaders(),
          signal: AbortSignal.timeout(5000), // 5 second timeout
        });
      }

      if (response.ok) {
        setOpensearchStatus('connected');
      } else {
        setOpensearchStatus('disconnected');
      }
    } catch (error) {
      console.warn('OpenSearch connection check failed:', error);
      setOpensearchStatus('disconnected');
    }
  }, [opensearchUrl, selectedPage, getOpensearchHeaders]);

  // Trigger connection checks when page changes
  React.useEffect(() => {
    if (selectedPage === 'metrics') {
      checkPrometheusConnection(false, true); // Force check on page change
    } else if (selectedPage === 'logs') {
      checkOpensearchConnection();
    }
  }, [selectedPage, checkPrometheusConnection, checkOpensearchConnection]);

  // Monitor Prometheus status and reset if stuck
  React.useEffect(() => {
    if (prometheusStatus === 'checking') {
      const resetTimeout = setTimeout(() => {
        console.warn('Prometheus status stuck in checking, forcing retry...');
        checkPrometheusConnection(true, true); // Force retry
      }, 15000); // 15 second timeout

      return () => clearTimeout(resetTimeout);
    }
  }, [prometheusStatus, checkPrometheusConnection]);

  // Check connection on mount and when page changes
  React.useEffect(() => {
    // Initial connection checks
    if (selectedPage === 'metrics') {
      checkPrometheusConnection();
    } else if (selectedPage === 'logs') {
      checkOpensearchConnection();
    }

    // Set up interval for active page only
    let interval: NodeJS.Timeout | null = null;
    if (selectedPage === 'metrics') {
      interval = setInterval(() => checkPrometheusConnection(), 30000);
    } else if (selectedPage === 'logs') {
      interval = setInterval(() => checkOpensearchConnection(), 30000);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [selectedPage]); // Only depend on selectedPage

  // Update query when initialQuery changes
  React.useEffect(() => {
    if (initialQuery !== undefined) {
      isUpdatingFromParent.current = true;
      setQuery(initialQuery);
      // Reset flag after state update
      setTimeout(() => {
        isUpdatingFromParent.current = false;
      }, 0);
    }
  }, [initialQuery]); // Only depend on initialQuery, not query

  // Notify parent when query changes (but only from user input)
  React.useEffect(() => {
    if (onQueryUpdate && !isUpdatingFromParent.current) {
      onQueryUpdate(selectedPage, query);
    }
  }, [query, selectedPage]); // Remove onQueryUpdate from dependencies to prevent loop

  // Update URL when query changes (debounced)
  React.useEffect(() => {
    const timeoutId = setTimeout(() => {
      if (query.trim()) {
        const urlParams = new URLSearchParams(window.location.search);
        urlParams.set('query', query.trim());
        const newUrl = `${window.location.pathname}?${urlParams.toString()}`;
        window.history.replaceState({}, '', newUrl);
      } else {
        // Remove query parameter if empty
        const urlParams = new URLSearchParams(window.location.search);
        urlParams.delete('query');
        const newUrl = urlParams.toString()
          ? `${window.location.pathname}?${urlParams.toString()}`
          : window.location.pathname;
        window.history.replaceState({}, '', newUrl);
      }
    }, 500); // 500ms debounce

    return () => clearTimeout(timeoutId);
  }, [query]);

  const queryPrometheus = async (promqlQuery: string) => {
    const prometheusUrl = process.env.REACT_APP_PROMETHEUS_URL || 'http://localhost:9090';
    const endTime = Math.floor(Date.now() / 1000);
    const startTime = endTime - 3600; // 1 hour ago
    const step = 60; // 1 minute step

    try {
      const url = `${prometheusUrl}/api/v1/query_range?query=${encodeURIComponent(promqlQuery)}&start=${startTime}&end=${endTime}&step=${step}`;

      const response = await fetch(url, {
        method: 'GET',
        headers: {
          'Accept': 'application/json',
        },
      });

      const result = await response.json();

      if (!response.ok) {
        // Create detailed error with response data
        const error = new Error(`Prometheus query failed: ${response.status} ${response.statusText}`);
        (error as any).responseData = result;
        (error as any).statusCode = response.status;
        throw error;
      }

      if (result.status !== 'success') {
        // Create detailed error with Prometheus error response
        const error = new Error(`Prometheus query error: ${result.error || 'Unknown error'}`);
        (error as any).responseData = result;
        (error as any).errorType = result.errorType;
        throw error;
      }

      return {
        title: "Metrics Visualization",
        data: result.data,
        query: promqlQuery,
        metadata: {
          timeRange: "1h",
          step: "1m",
          resultType: result.data.resultType
        }
      };
    } catch (error) {
      console.error('Prometheus query error:', error);
      throw error;
    }
  };

  const queryOpensearch = async (pplQuery: string, signal?: AbortSignal) => {
    const opensearchUrl = process.env.REACT_APP_OPENSEARCH_URL || 'http://localhost:9200';

    try {
      // First try PPL endpoint
      const response = await fetch(`${opensearchUrl}/_plugins/_ppl`, {
        method: 'POST',
        headers: getOpensearchHeaders(),
        body: JSON.stringify({
          query: pplQuery
        }),
        signal: signal
      });

      const result = await response.json();

      if (!response.ok) {
        // Check if it's a PPL plugin not available error (404 or 500)
        if (response.status === 404 || response.status === 500) {
          const error = new Error(`PPL plugin not available on this OpenSearch cluster (${response.status}). This AWS OpenSearch cluster may not have PPL enabled.`);
          (error as any).responseData = {
            suggestion: "AWS OpenSearch clusters may not have PPL plugin enabled by default. Consider using OpenSearch Query DSL or enabling PPL plugin.",
            pplQuery: pplQuery,
            alternativeEndpoint: `${opensearchUrl}/_search`,
            statusCode: response.status
          };
          throw error;
        }

        // Create detailed error with response data
        const error = new Error(`OpenSearch query failed: ${response.status} ${response.statusText}`);
        (error as any).responseData = result;
        (error as any).statusCode = response.status;
        throw error;
      }

      if (result.error) {
        // Handle specific PPL errors
        if (result.error.type === 'NoSuchElementException') {
          const error = new Error(`PPL query error: No data found. Check your index name and query syntax.`);
          (error as any).responseData = result;
          (error as any).errorType = result.error.type;
          (error as any).suggestion = "Try: source=your_actual_index_name | head 10 (replace 'your_actual_index_name' with an actual index)";
          throw error;
        }

        // Create detailed error with OpenSearch error response
        const error = new Error(`OpenSearch PPL error: ${result.error.reason || 'Unknown error'}`);
        (error as any).responseData = result;
        (error as any).errorType = result.error.type;
        throw error;
      }

      return {
        title: "Logs Visualization",
        data: result,
        query: pplQuery,
        metadata: {
          timeRange: "query-dependent",
          source: "OpenSearch PPL",
          resultType: "logs"
        }
      };
    } catch (error) {
      console.error('OpenSearch query error:', error);
      throw error;
    }
  };

  const handleExecuteQuery = async () => {
    if (!query.trim() || isExecuting) return;

    // Collapse the series explorer to give more space for results
    if (selectedPage === 'metrics' && showExplorer) {
      setShowExplorer(false);
    }

    const newResult: QueryResult = {
      id: `query-${Date.now()}`,
      query: query.trim(),
      timestamp: new Date(),
    };

    setIsExecuting(true);
    setPageResults(prev => ({
      ...prev,
      [selectedPage]: newResult
    }));

    try {
      let responseData: any;

      if (selectedPage === 'metrics') {
        // Query Prometheus for metrics
        responseData = await queryPrometheus(query.trim());
      } else if (selectedPage === 'logs') {
        // Query OpenSearch for logs
        responseData = await queryOpensearch(query.trim());
      } else {
        // For traces, use mock data for now
        responseData = {
          title: `${selectedPage.charAt(0).toUpperCase() + selectedPage.slice(1)} Visualization`,
          data: {
            result: [
              {
                metric: { __name__: query, service: selectedPage },
                values: Array.from({ length: 20 }, (_, i) => [
                  Date.now() / 1000 - (20 - i) * 60,
                  (Math.random() * 100).toFixed(2)
                ])
              }
            ]
          },
          query: query,
          metadata: {
            timeRange: "1h",
            step: "1m",
            type: selectedPage
          }
        };
      }

      setPageResults(prev => ({
        ...prev,
        [selectedPage]: prev[selectedPage] ? { ...prev[selectedPage], data: responseData } : null
      }));
    } catch (error: any) {
      console.error('Query execution error:', error);
      const errorMessage = selectedPage === 'metrics'
        ? `Prometheus query failed: ${error.message || 'Unknown error'}`
        : `${selectedPage} query failed: ${error.message || 'Unknown error'}`;

      setPageResults(prev => ({
        ...prev,
        [selectedPage]: prev[selectedPage] ? {
          ...prev[selectedPage],
          error: errorMessage,
          errorDetails: error.responseData || null
        } : null
      }));
    } finally {
      setIsExecuting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      handleExecuteQuery();
    }
  };

  const clearResults = () => {
    setPageResults(prev => ({
      ...prev,
      [selectedPage]: null
    }));
  };

  // Track processed trigger queries to prevent infinite loops
  const processedTriggerQuery = React.useRef<string | null>(null);

  // Handle trigger query execution from ChatAssistant
  React.useEffect(() => {
    if (triggerQuery && triggerQuery.trim() && triggerQuery !== processedTriggerQuery.current) {
      processedTriggerQuery.current = triggerQuery;
      setQuery(triggerQuery);

      // Execute the query automatically with the triggered query
      const executeTriggeredQuery = async () => {
        setIsExecuting(true);
        try {
          let result;
          if (selectedPage === 'metrics') {
            result = await queryPrometheus(triggerQuery);
          } else if (selectedPage === 'logs') {
            result = await queryOpensearch(triggerQuery);
          } else {
            throw new Error('Traces not implemented yet');
          }

          const newResult: QueryResult = {
            id: Date.now().toString(),
            query: triggerQuery,
            timestamp: new Date(),
            data: result
          };

          setPageResults(prev => ({
            ...prev,
            [selectedPage]: newResult
          }));

          if (onQueryTriggered) {
            onQueryTriggered();
          }
        } catch (error: any) {
          const errorResult: QueryResult = {
            id: Date.now().toString(),
            query: triggerQuery,
            timestamp: new Date(),
            error: error.message || 'An error occurred',
            errorDetails: error
          };

          setPageResults(prev => ({
            ...prev,
            [selectedPage]: errorResult
          }));
        } finally {
          setIsExecuting(false);
        }
      };

      // Small delay to ensure state is updated
      setTimeout(executeTriggeredQuery, 100);
    }
  }, [triggerQuery, selectedPage, onQueryTriggered]);

  // Handle keyboard shortcuts for modal
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isMaximized) {
        setIsMaximized(false);
      }
    };

    if (isMaximized) {
      document.addEventListener('keydown', handleKeyDown);
      // Prevent body scroll when modal is open
      document.body.style.overflow = 'hidden';
    }

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = 'unset';
    };
  }, [isMaximized]);

  // Cleanup timeout refs on unmount
  React.useEffect(() => {
    return () => {
      if (prometheusRetryTimeoutRef.current) {
        clearTimeout(prometheusRetryTimeoutRef.current);
      }
      if (opensearchRetryTimeoutRef.current) {
        clearTimeout(opensearchRetryTimeoutRef.current);
      }
    };
  }, []);

  return (
    <div className="observability-platform">
      <div className="platform-header">
        <div className="header-content">
          <h1>ExampleOps Platform - {selectedPage.charAt(0).toUpperCase() + selectedPage.slice(1)}</h1>
          <p>
            {selectedPage === 'metrics' && 'Query and visualize your application metrics and performance data'}
            {selectedPage === 'logs' && 'Search and analyze your application logs and events'}
            {selectedPage === 'traces' && 'Explore distributed traces and request flows'}
          </p>
        </div>
      </div>

      {selectedPage === 'metrics' && (
        <div className="connection-status-bar">
          <div className="connection-info">
            <span className="connection-label">Prometheus:</span>
            <span className="connection-url">{prometheusUrl}</span>
            <div className={`connection-indicator ${prometheusStatus}`}>
              <span className="status-dot"></span>
              <span className="status-text">
                {prometheusStatus === 'checking' && 'Checking...'}
                {prometheusStatus === 'connected' && 'Connected'}
                {prometheusStatus === 'disconnected' && 'Disconnected'}
              </span>
            </div>
          </div>
          {prometheusStatus === 'disconnected' && (
            <button
              className="retry-connection-btn"
              onClick={() => checkPrometheusConnection(true)}
            >
              Retry
            </button>
          )}
        </div>
      )}

      {selectedPage === 'logs' && (
        <div className="connection-status-bar">
          <div className="connection-info">
            <span className="connection-label">OpenSearch:</span>
            <span className="connection-url">{opensearchUrl}</span>
            <div className={`connection-indicator ${opensearchStatus}`}>
              <span className="status-dot"></span>
              <span className="status-text">
                {opensearchStatus === 'checking' && 'Checking...'}
                {opensearchStatus === 'connected' && 'Connected'}
                {opensearchStatus === 'disconnected' && 'Disconnected'}
              </span>
            </div>
          </div>
          {opensearchStatus === 'disconnected' && (
            <button
              className="retry-connection-btn"
              onClick={() => checkOpensearchConnection(true)}
            >
              Retry
            </button>
          )}
        </div>
      )}

      {/* Prometheus Series Explorer - Three-box interface */}
      {selectedPage === 'metrics' && prometheusStatus === 'connected' && (
        <div className="prometheus-explorer">
          <div className="explorer-header">
            <div className="explorer-title" onClick={() => setShowExplorer(!showExplorer)}>
              <h4>
                Prometheus Series Explorer
                {availableMetrics.length > 0 && (
                  <span className="series-count-pill">({availableMetrics.length})</span>
                )}
                <span className="toggle-icon">{showExplorer ? '▼' : '▶'}</span>
              </h4>
              <p>Browse series, labels, and values to build your query</p>
            </div>
          </div>

          {showExplorer && (
            <div className="explorer-boxes">
            {/* Box 1: Series List */}
            <div className="explorer-box">
              <div className="box-header">
                <h5>Series ({availableMetrics.length})</h5>
                {loadingMetrics && <span className="loading-spinner"></span>}
              </div>
              <div className="box-content">
                {availableMetrics.length > 0 ? (
                  availableMetrics.map((metric, i) => (
                    <div
                      key={i}
                      className={`explorer-item ${selectedMetric === metric ? 'selected' : ''}`}
                      onClick={() => handleMetricSelect(metric)}
                      title={`Click to explore labels for: ${metric}`}
                    >
                      {metric}
                    </div>
                  ))
                ) : (
                  <div className="empty-box">
                    {loadingMetrics ? 'Loading series...' : 'No series available'}
                  </div>
                )}
              </div>
            </div>

            {/* Box 2: Labels List */}
            <div className="explorer-box">
              <div className="box-header">
                <h5>Labels ({availableLabels.length})</h5>
                {loadingLabels && <span className="loading-spinner"></span>}
              </div>
              <div className="box-content">
                {selectedMetric ? (
                  availableLabels.length > 0 ? (
                    availableLabels.map((label, i) => (
                      <div
                        key={i}
                        className={`explorer-item ${selectedLabel === label ? 'selected' : ''}`}
                        onClick={() => handleLabelSelect(label)}
                        title={`Click to explore values for label: ${label}`}
                      >
                        {label}
                      </div>
                    ))
                  ) : (
                    <div className="empty-box">
                      {loadingLabels ? 'Loading labels...' : 'No labels available'}
                    </div>
                  )
                ) : (
                  <div className="empty-box">Select a series first</div>
                )}
              </div>
            </div>

            {/* Box 3: Label Values List */}
            <div className="explorer-box">
              <div className="box-header">
                <h5>Values ({availableLabelValues.length})</h5>
                {loadingLabelValues && <span className="loading-spinner"></span>}
              </div>
              <div className="box-content">
                {selectedLabel ? (
                  availableLabelValues.length > 0 ? (
                    availableLabelValues.map((value, i) => (
                      <div
                        key={i}
                        className="explorer-item"
                        onClick={() => handleLabelValueSelect(value)}
                        title={`Click to build query: ${selectedMetric}{${selectedLabel}="${value}"}`}
                      >
                        {value}
                      </div>
                    ))
                  ) : (
                    <div className="empty-box">
                      {loadingLabelValues ? 'Loading values...' : 'No values available'}
                    </div>
                  )
                ) : (
                  <div className="empty-box">Select a label first</div>
                )}
              </div>
            </div>
          </div>
          )}
        </div>
      )}

      {/* OpenSearch Indices Explorer */}
      {selectedPage === 'logs' && opensearchStatus === 'connected' && (
        <div className="prometheus-explorer">
          <div className="explorer-header">
            <div className="explorer-title" onClick={() => setShowIndicesExplorer(!showIndicesExplorer)}>
              <h4>
                OpenSearch Indices Explorer
                {availableIndices.length > 0 && (
                  <span className="series-count-pill">({availableIndices.length})</span>
                )}
                <span className="toggle-icon">{showIndicesExplorer ? '▼' : '▶'}</span>
              </h4>
              <p>Browse available indices to build your PPL query</p>
            </div>
          </div>

          {showIndicesExplorer && (
            <div className="explorer-boxes">
              {/* Single Box: Indices List */}
              <div className="explorer-box opensearch-single-box">
                <div className="box-header">
                  <h5>Indices ({availableIndices.length})</h5>
                  {loadingIndices && <span className="loading-spinner"></span>}
                </div>
                <div className="box-content">
                  {availableIndices.length > 0 ? (
                    availableIndices.map((index, i) => (
                      <div
                        key={i}
                        className="explorer-item"
                        onClick={() => {
                          setQuery(`source=${index} | head 10`);
                          setShowIndicesExplorer(false);
                        }}
                        title={`Click to use in query: source=${index} | head 10`}
                      >
                        {index}
                      </div>
                    ))
                  ) : (
                    <div className="empty-box">
                      {loadingIndices ? 'Loading indices...' : 'No indices available'}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="query-section">
        <div className="query-input-container">
          <div className="query-label-row">
            <label htmlFor="query-input" className="query-label">
              {selectedPage === 'metrics' && 'Metrics Query'}
              {selectedPage === 'logs' && 'Log Query'}
              {selectedPage === 'traces' && 'Trace Query'}
            </label>
            <span className="query-hint-inline">
              Press Cmd/Ctrl + Enter to execute
            </span>
          </div>
          <div className="query-input-wrapper">
            <textarea
              id="query-input"
              className="query-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                selectedPage === 'metrics'
                  ? "Enter PromQL query (e.g., cpu_usage, memory_usage, http_requests_total)...\n\nOr use Prometheus Series Explorer above to build your query"
                  : selectedPage === 'logs'
                  ? "Enter PPL query (e.g., source=logs-* | head 10)...\n\nOr use OpenSearch Indices Explorer above to build your query\n\nNote: PPL plugin may not be available on all AWS clusters"
                  : "Enter trace query (e.g., service:checkout, operation:payment, duration:>1s)..."
              }
              rows={3}
            />
            <div className="query-actions">
              <button
                className="execute-button"
                onClick={handleExecuteQuery}
                disabled={!query.trim() || isExecuting}
              >
                {isExecuting ? 'Executing...' : 'Execute'}
              </button>
              {currentResult && (
                <button
                  className="clear-button"
                  onClick={clearResults}
                >
                  Clear Results
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="results-section">
        {!currentResult ? (
          <div className="empty-state">
            <div className="empty-icon">📊</div>
            <h3>No queries executed yet</h3>
            <p>Enter a query above and click Execute to see visualizations</p>
          </div>
        ) : (
          <div className="result-item">
            <div className="result-content">
              {currentResult.error ? (
                <div className="error-message">
                  <span className="error-icon">⚠️</span>
                  <div className="error-text">{currentResult.error}</div>
                  {currentResult.errorDetails && (
                    <div className="error-details">
                      <div className="error-details-label">Response Details:</div>
                      <div className="error-response-container">
                        <pre className="error-response">
                          {JSON.stringify(currentResult.errorDetails, null, 2)}
                        </pre>
                      </div>
                    </div>
                  )}
                </div>
              ) : currentResult.data ? (
                <div className="visualization-container">
                  {/* Detect data type and render appropriate visualization */}
                  {/* Check if data is already structured (has title, data, query) or raw (has schema, datarows) */}
                  {(currentResult.data.schema && currentResult.data.datarows) ||
                   (currentResult.data.data && currentResult.data.data.schema && currentResult.data.data.datarows) ? (
                    <div className="visualization-container">
                      <button
                        className="maximize-button-overlay"
                        onClick={() => setIsMaximized(true)}
                        title="Maximize visualization"
                      >
                        ⛶
                      </button>
                      <LogsVisualization
                        data={
                          currentResult.data.title ?
                            // Data is already structured
                            currentResult.data :
                            // Data is raw, need to structure it
                            {
                              title: selectedPage === 'logs' ? 'Logs Visualization' : 'Data Visualization',
                              query: currentResult.query,
                              data: currentResult.data,
                              metadata: {
                                timestamp: Date.now() / 1000,
                                source: 'OpenSearch PPL'
                              }
                            }
                        }
                      />
                    </div>
                  ) : (currentResult.data.result !== undefined) ||
                       (currentResult.data.data && currentResult.data.data.result !== undefined) ? (
                    <div className="visualization-container">
                      <button
                        className="maximize-button-overlay"
                        onClick={() => setIsMaximized(true)}
                        title="Maximize visualization"
                      >
                        ⛶
                      </button>
                      <GraphVisualization
                        data={
                          currentResult.data.title ?
                            // Data is already structured
                            currentResult.data :
                            // Data is raw, need to structure it
                            {
                              title: selectedPage === 'metrics' ? 'Metrics Visualization' : 'Data Visualization',
                              query: currentResult.query,
                              data: currentResult.data,
                              metadata: {
                                timestamp: Date.now() / 1000,
                                source: 'Prometheus'
                              }
                            }
                        }
                      />
                    </div>
                  ) : (
                    <div className="unsupported-data">
                      <span className="error-icon">⚠️</span>
                      <div className="error-text">Unsupported data format</div>
                      <div className="error-details">
                        <pre>{JSON.stringify(currentResult.data, null, 2)}</pre>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="loading-placeholder">
                  <div className="loading-spinner"></div>
                  <span>Executing query...</span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Maximized Graph Modal */}
      {isMaximized && currentResult?.data && (
        <div className="graph-modal-overlay" onClick={() => setIsMaximized(false)}>
          <div className="graph-modal" onClick={(e) => e.stopPropagation()}>
            <div className="graph-modal-header">
              <div className="graph-modal-title">
                <h3>
                  {selectedPage === 'metrics' ? '📊 Metrics Visualization' :
                   selectedPage === 'logs' ? '📝 Logs Visualization' :
                   '🔍 Traces Visualization'}
                </h3>
                <div className="graph-modal-query">
                  Query: <code>{currentResult.query}</code>
                </div>
              </div>
              <button
                className="close-modal-button"
                onClick={() => setIsMaximized(false)}
                title="Close maximized view"
              >
                ✕
              </button>
            </div>
            <div className="graph-modal-content">
              {/* Detect data type and render appropriate visualization */}
              {(currentResult.data.schema && currentResult.data.datarows) ||
               (currentResult.data.data && currentResult.data.data.schema && currentResult.data.data.datarows) ? (
                <LogsVisualization
                  data={
                    currentResult.data.title ?
                      // Data is already structured
                      currentResult.data :
                      // Data is raw, need to structure it
                      {
                        title: selectedPage === 'logs' ? 'Logs Visualization' : 'Data Visualization',
                        query: currentResult.query,
                        data: currentResult.data,
                        metadata: {
                          timestamp: Date.now() / 1000,
                          source: 'OpenSearch PPL'
                        }
                      }
                  }
                />
              ) : (currentResult.data.result !== undefined) ||
                   (currentResult.data.data && currentResult.data.data.result !== undefined) ? (
                <GraphVisualization
                  data={
                    currentResult.data.title ?
                      // Data is already structured
                      currentResult.data :
                      // Data is raw, need to structure it
                      {
                        title: selectedPage === 'metrics' ? 'Metrics Visualization' : 'Data Visualization',
                        query: currentResult.query,
                        data: currentResult.data,
                        metadata: {
                          timestamp: Date.now() / 1000,
                          source: 'Prometheus'
                        }
                      }
                  }
                />
              ) : (
                <div className="unsupported-data">
                  <span className="error-icon">⚠️</span>
                  <div className="error-text">Unsupported data format</div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default MainContent;
