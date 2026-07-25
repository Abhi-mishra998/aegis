import React from 'react';
import { AlertTriangle } from 'lucide-react';

/**
 * TabErrorBoundary
 *
 * Scoped error boundary for tab-router pages (Settings, Policies, etc.).
 * Without this, a render error in a single tab body bubbles all the way
 * to the root ErrorBoundary, which paints a full-screen red overlay —
 * the user perceives the content area as "blank" because the overlay
 * covered everything. Catching at the tab level keeps the chrome + tab
 * bar usable and surfaces the actual error inline.
 *
 * Pass a `tabId` prop that changes when the active tab changes; the
 * boundary auto-resets its error state on tab change so users can try
 * a different tab without a manual refresh.
 */
class TabErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    // eslint-disable-next-line no-console
    console.error('Tab render error', this.props.tabId, error, errorInfo);
  }

  componentDidUpdate(prevProps) {
    if (prevProps.tabId !== this.props.tabId && this.state.hasError) {
      // eslint-disable-next-line react/no-direct-mutation-state
      this.setState({ hasError: false, error: null });
    }
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="max-w-3xl mx-auto py-8">
        <div className="flex items-start gap-3 p-4 rounded-xl border border-red-500/20 bg-red-500/[0.05]">
          <AlertTriangle size={16} className="text-red-400 shrink-0 mt-0.5" aria-hidden="true" />
          <div className="space-y-1">
            <div className="text-sm font-semibold text-red-300">
              This tab failed to render.
            </div>
            <div className="text-xs text-red-300/80 font-mono break-words">
              {this.state.error?.message || 'Unknown render error'}
            </div>
            <div className="text-[11px] text-neutral-500 mt-2">
              Try a different tab from the bar above. If every tab fails the
              same way, refresh the page — your session may have expired.
            </div>
          </div>
        </div>
      </div>
    );
  }
}

export default TabErrorBoundary;
