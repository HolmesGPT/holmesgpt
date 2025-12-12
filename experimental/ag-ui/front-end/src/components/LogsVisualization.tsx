import React from 'react';
import './LogsVisualization.css';

interface LogData {
  title: string;
  query?: string;
  data: {
    schema: Array<{
      name: string;
      type: string;
    }>;
    datarows: Array<Array<any>>;
    total?: number;
    size?: number;
  };
  metadata?: {
    timestamp?: number;
    source?: string;
  };
}

interface LogsVisualizationProps {
  data: LogData;
}

const LogsVisualization: React.FC<LogsVisualizationProps> = ({ data }) => {
  const { title, query, data: logData, metadata } = data;

  // Format cell value based on type and content
  const formatCellValue = (value: any, type: string, columnName: string) => {
    if (value === null || value === undefined || value === '') {
      return <span className="null-value">-</span>;
    }

    // Handle timestamp formatting
    if (type === 'timestamp' && typeof value === 'string') {
      try {
        const date = new Date(value);
        // Check if it's a valid date and not epoch 0
        if (date.getTime() > 0) {
          return <span className="timestamp-value">{date.toLocaleString()}</span>;
        }
      } catch {
        // Fall through to default handling
      }
    }

    // Handle object/struct types
    if (typeof value === 'object' && value !== null) {
      return <span className="object-value">{JSON.stringify(value)}</span>;
    }

    // Handle severity levels with colors
    if (columnName === 'severityText' || columnName === 'severityNumber') {
      const severity = String(value).toLowerCase();
      let className = 'severity-value';

      if (severity.includes('error') || severity.includes('err') || value === 3) {
        className += ' severity-error';
      } else if (severity.includes('warn') || value === 2) {
        className += ' severity-warn';
      } else if (severity.includes('info') || value === 1 || value === 9) {
        className += ' severity-info';
      } else if (severity.includes('debug') || value === 0) {
        className += ' severity-debug';
      }

      return <span className={className}>{String(value)}</span>;
    }

    // Handle long text content (like log messages)
    const stringValue = String(value);
    if (columnName === 'body' || columnName === 'message') {
      return (
        <span
          className="log-message"
          title={stringValue}
        >
          {stringValue}
        </span>
      );
    }

    return <span className="text-value">{stringValue}</span>;
  };

  // Get column width class based on column type and name
  const getColumnClass = (columnName: string, type: string) => {
    const baseClass = 'log-header';

    if (columnName === 'body' || columnName === 'message') {
      return `${baseClass} column-wide`;
    }

    if (type === 'timestamp' || columnName.includes('Time')) {
      return `${baseClass} column-timestamp`;
    }

    if (columnName === 'traceId' || columnName === 'spanId') {
      return `${baseClass} column-id`;
    }

    if (columnName === 'serviceName' || columnName === 'service') {
      return `${baseClass} column-service`;
    }

    return `${baseClass} column-normal`;
  };

  if (!logData?.schema || !logData?.datarows) {
    return (
      <div className="logs-visualization">
        <div className="logs-container">
          <div className="logs-header">
            <h4>{title}</h4>
            {query && <code className="query">{query}</code>}
            <div className="data-type-indicator">📝 Log Data</div>
          </div>
          <div className="no-data">No log data available</div>
        </div>
      </div>
    );
  }

  const { schema, datarows, total, size } = logData;

  // Reorder columns to put timestamp columns first
  const reorderColumnsForTimestamp = () => {
    const timestampColumns: Array<{index: number, column: any, priority: number}> = [];
    const otherColumns: Array<{index: number, column: any}> = [];

    schema.forEach((column, index) => {
      const columnName = column.name.toLowerCase();
      const isTimestamp = column.type === 'timestamp' ||
                         columnName.includes('time') ||
                         columnName.includes('timestamp') ||
                         columnName === '@timestamp';

      if (isTimestamp) {
        // Priority: "time" gets highest priority (0), then alphabetical
        let priority = 1;
        if (columnName === 'time') priority = 0;
        else if (columnName === '@timestamp') priority = 0.5;

        timestampColumns.push({ index, column, priority });
      } else {
        otherColumns.push({ index, column });
      }
    });

    // Sort timestamp columns by priority, then alphabetically
    timestampColumns.sort((a, b) => {
      if (a.priority !== b.priority) return a.priority - b.priority;
      return a.column.name.localeCompare(b.column.name);
    });

    // Create new column order
    const reorderedColumns = [
      ...timestampColumns.map(item => item.column),
      ...otherColumns.map(item => item.column)
    ];

    // Create index mapping for data reordering
    const indexMapping = [
      ...timestampColumns.map(item => item.index),
      ...otherColumns.map(item => item.index)
    ];

    return { reorderedColumns, indexMapping };
  };

  const { reorderedColumns, indexMapping } = reorderColumnsForTimestamp();

  // Reorder data rows according to the new column order
  const reorderedDatarows = datarows.map(row =>
    indexMapping.map(originalIndex => row[originalIndex])
  );

  return (
    <div className="logs-visualization">
      <div className="logs-container">
        <div className="logs-header">
          <h4>{title}</h4>
          {query && <code className="query">{query}</code>}
          <div className="data-type-indicator">📝 Log Data</div>
        </div>

        <div className="log-table-container">
          <div className="log-table-header">
            <div className="log-stats">
              Showing {size || datarows.length} of {total || datarows.length} log entries
            </div>
            <div className="log-actions">
              <button
                className="export-btn"
                onClick={() => {
                  // Simple CSV export functionality
                  const csvContent = [
                    reorderedColumns.map(col => col.name).join(','),
                    ...reorderedDatarows.map(row =>
                      row.map(cell =>
                        typeof cell === 'string' && cell.includes(',')
                          ? `"${cell.replace(/"/g, '""')}"`
                          : String(cell || '')
                      ).join(',')
                    )
                  ].join('\n');

                  const blob = new Blob([csvContent], { type: 'text/csv' });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `logs-${new Date().toISOString().split('T')[0]}.csv`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
                title="Export logs as CSV"
              >
                📥 Export
              </button>
            </div>
          </div>

          <div className="log-table-wrapper">
            <table className="log-table">
              <thead>
                <tr>
                  {reorderedColumns.map((column, index) => (
                    <th key={index} className={getColumnClass(column.name, column.type)}>
                      <div className="column-info">
                        <span className="column-name">{column.name}</span>
                        <span className="column-type">{column.type}</span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {reorderedDatarows.map((row, rowIndex) => (
                  <tr key={rowIndex} className="log-row">
                    {row.map((cell, cellIndex) => (
                      <td
                        key={cellIndex}
                        className="log-cell"
                        data-field={reorderedColumns[cellIndex]?.name}
                      >
                        {formatCellValue(cell, reorderedColumns[cellIndex]?.type || 'string', reorderedColumns[cellIndex]?.name || '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {metadata && (
          <div className="logs-metadata">
            <small>
              Source: {metadata.source || 'OpenSearch'} |
              Generated: {metadata.timestamp ? new Date(metadata.timestamp * 1000).toLocaleString() : new Date().toLocaleString()}
            </small>
          </div>
        )}
      </div>
    </div>
  );
};

export default LogsVisualization;
