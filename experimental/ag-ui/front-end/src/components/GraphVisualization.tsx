import React from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  TimeScale,
  ChartOptions,
  ChartData,
  TooltipItem,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import 'chartjs-adapter-date-fns';
import './GraphVisualization.css';

// Register Chart.js components
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  TimeScale
);

interface GraphData {
  title: string;
  query?: string;
  data: {
    result: Array<{
      metric: Record<string, string>;
      values: Array<[number, string]>;
    }>;
  };
  metadata?: {
    timestamp?: number;
    source?: string;
    start_time?: string;
    end_time?: string;
    step?: string;
  };
}

interface GraphVisualizationProps {
  data: GraphData;
}

const GraphVisualization: React.FC<GraphVisualizationProps> = ({ data }) => {
  const { title, query, data: graphData, metadata } = data;

  // Generate colors for different series (Grafana-like palette)
  const generateColor = (index: number) => {
    const colors = [
      '#7EB26D', // Green
      '#EAB839', // Yellow
      '#6ED0E0', // Light Blue
      '#EF843C', // Orange
      '#E24D42', // Red
      '#1F78C1', // Blue
      '#BA43A9', // Purple
      '#705DA0', // Dark Purple
      '#508642', // Dark Green
      '#CCA300', // Dark Yellow
    ];
    return colors[index % colors.length];
  };

  // Generate series label from metric
  const generateSeriesLabel = (metric: Record<string, string>, index: number) => {
    console.log('Metric data for series', index, ':', metric);

    // First try to use __name__ if it exists
    if (metric.__name__) {
      // If there are other meaningful labels, combine them
      const filteredMetric = Object.entries(metric).filter(
        ([key]) => key !== '__name__'
      );

      if (filteredMetric.length > 0) {
        const labels = filteredMetric
          .map(([key, value]) => `${key}="${value}"`)
          .join(', ');
        return `${metric.__name__}{${labels}}`;
      }

      // Just return the metric name
      return metric.__name__;
    }

    // If no __name__, use all available labels (including job)
    const allLabels = Object.entries(metric);

    if (allLabels.length > 0) {
      // For single label, just show the value part if it's descriptive
      if (allLabels.length === 1) {
        const [key, value] = allLabels[0];
        // If it's a job label with a descriptive path, extract the service name
        if (key === 'job' && value.includes('/')) {
          return value.split('/').pop() || value;
        }
        return value;
      }

      // For multiple labels, show key=value format
      return allLabels
        .map(([key, value]) => `${key}="${value}"`)
        .join(', ');
    }

    // Last resort: use series index
    return `Series ${index + 1}`;
  };

  // Prepare Chart.js data
  const prepareChartData = (): ChartData<'line'> => {
    if (!graphData?.result || graphData.result.length === 0) {
      return { datasets: [] };
    }

    const datasets = graphData.result.map((series, index) => {
      const color = generateColor(index);
      const label = generateSeriesLabel(series.metric, index);

      const dataPoints = series.values?.map(([timestamp, value]) => ({
        x: timestamp * 1000, // Convert to milliseconds
        y: parseFloat(value),
      })) || [];

      return {
        label,
        data: dataPoints,
        borderColor: color,
        backgroundColor: color + '20', // Add transparency
        borderWidth: 2,
        fill: false,
        tension: 0.1,
        pointRadius: 2,
        pointHoverRadius: 4,
      };
    });

    return { datasets };
  };

  // Chart.js options (Grafana-like styling)
  const chartOptions: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index',
      intersect: false,
    },
    plugins: {
      legend: {
        display: false, // Disable default legend, we'll create a custom one
      },
      tooltip: {
        mode: 'index',
        intersect: false,
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        titleColor: '#fff',
        bodyColor: '#fff',
        borderColor: '#666',
        borderWidth: 1,
        itemSort: (a: TooltipItem<'line'>, b: TooltipItem<'line'>) => {
          // Sort by value descending (highest to lowest)
          return b.parsed.y - a.parsed.y;
        },
        callbacks: {
          title: (context: TooltipItem<'line'>[]) => {
            const date = new Date(context[0].parsed.x);
            return date.toLocaleString();
          },
          label: (context: TooltipItem<'line'>) => {
            return `${context.dataset.label}: ${context.parsed.y}`;
          },
        },
      },
    },
    scales: {
      x: {
        type: 'time',
        time: {
          displayFormats: {
            minute: 'HH:mm',
            hour: 'HH:mm',
            day: 'MMM dd',
          },
        },
        grid: {
          color: 'rgba(0, 0, 0, 0.1)',
        },
        ticks: {
          color: '#666',
        },
      },
      y: {
        grid: {
          color: 'rgba(0, 0, 0, 0.1)',
        },
        ticks: {
          color: '#666',
        },
      },
    },
  };

  // Determine if legend should be vertical based on number of series and label length
  const shouldUseVerticalLegend = (datasets: any[]) => {
    const seriesCount = datasets.length;
    const maxLabelLength = Math.max(...datasets.map(dataset =>
      dataset.label ? dataset.label.length : 0
    ));
    const hasLongLabels = datasets.some(dataset =>
      dataset.label && dataset.label.length > 30  // Lowered from 40 to 30
    );

    console.log('Legend layout check:', {
      seriesCount,
      maxLabelLength,
      hasLongLabels,
      sampleLabels: datasets.slice(0, 3).map(d => d.label)
    });

    // Use vertical layout if:
    // - More than 5 series (lowered from 6), OR
    // - Any label is longer than 30 characters (lowered from 40), OR
    // - More than 3 series AND any label is longer than 20 characters
    const shouldBeVertical = seriesCount > 5 ||
           hasLongLabels ||
           (seriesCount > 3 && datasets.some(dataset =>
             dataset.label && dataset.label.length > 20
           ));

    console.log('Should use vertical legend:', shouldBeVertical);
    return shouldBeVertical;
  };

  const chartData = prepareChartData();

  if (!graphData?.result || graphData.result.length === 0) {
    return (
      <div className="graph-visualization">
        <div className="graph-container">
          <div className="graph-header">
            <h4>{title}</h4>
            {query && <code className="query">{query}</code>}
          </div>
          <div className="no-data">No data available</div>
        </div>
      </div>
    );
  }

  return (
    <div className="graph-visualization">
      <div className="graph-container">
        <div className="graph-header">
          <h4>{title}</h4>
          {query && <code className="query">{query}</code>}
        </div>

        <div className="chart-container">
          <Line data={chartData} options={chartOptions} />
        </div>

        {/* Custom scrollable legend */}
        <div className="custom-legend">
          <div className={`legend-items ${shouldUseVerticalLegend(chartData.datasets) ? 'vertical' : ''}`}>
            {chartData.datasets.map((dataset, index) => (
              <div key={index} className="legend-item">
                <div
                  className="legend-color"
                  style={{ backgroundColor: dataset.borderColor as string }}
                ></div>
                <span className="legend-label">{dataset.label}</span>
              </div>
            ))}
          </div>
        </div>

        {metadata && (
          <div className="graph-metadata">
            <small>
              Source: {metadata.source || 'Unknown'} |
              Generated: {metadata.timestamp ? new Date(metadata.timestamp * 1000).toLocaleString() : 'Unknown'}
              {metadata.start_time && metadata.end_time && (
                <> | Range: {new Date(metadata.start_time).toLocaleString()} - {new Date(metadata.end_time).toLocaleString()}</>
              )}
            </small>
          </div>
        )}
      </div>
    </div>
  );
};

export default GraphVisualization;
