import React, { ReactNode, ReactElement } from 'react';
import { AlertCircle } from 'lucide-react';

interface Props {
  children: ReactNode;
  fallback?: (error: Error, retry: () => void) => ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

/**
 * ErrorBoundary component catches errors in child components
 * Prevents entire app from crashing due to child component failures
 */
export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error('ErrorBoundary caught an error:', error);
  }

  retry = () => {
    this.setState({ hasError: false, error: undefined });
  };

  render(): ReactElement {
    if (this.state.hasError && this.state.error) {
      return this.props.fallback ? (
        <>{this.props.fallback(this.state.error, this.retry)}</>
      ) : (
        <div
          style={{
            padding: '20px',
            backgroundColor: '#fff3cd',
            border: '1px solid #ffc107',
            borderRadius: '4px',
            display: 'flex',
            gap: '12px',
            alignItems: 'flex-start',
          }}
        >
          <AlertCircle size={20} style={{ color: '#ff6b6b', flexShrink: 0, marginTop: 2 }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, color: '#333', marginBottom: 4 }}>
              An error occurred in this section
            </div>
            <div
              style={{
                fontSize: '12px',
                color: '#666',
                fontFamily: 'monospace',
                marginBottom: 8,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {this.state.error.message}
            </div>
            <button
              onClick={this.retry}
              style={{
                padding: '6px 12px',
                backgroundColor: '#007bff',
                color: 'white',
                border: 'none',
                borderRadius: '3px',
                cursor: 'pointer',
                fontSize: '12px',
                fontWeight: 500,
              }}
            >
              Retry
            </button>
          </div>
        </div>
      );
    }

    return <>{this.props.children}</>;
  }
}
