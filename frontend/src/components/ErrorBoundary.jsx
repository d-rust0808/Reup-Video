import React from 'react';
import { AlertTriangle, RefreshCw, Trash2 } from 'lucide-react';

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('[React ErrorBoundary caught error]:', error, errorInfo);
    this.setState({ errorInfo });
  }

  componentDidMount() {
    if (!import.meta.hot) return;
    this._onHotUpdate = () => {
      if (this.state.hasError) {
        this.setState({ hasError: false, error: null, errorInfo: null });
      }
    };
    import.meta.hot.on('vite:afterUpdate', this._onHotUpdate);
  }

  componentWillUnmount() {
    if (import.meta.hot && this._onHotUpdate) {
      import.meta.hot.off('vite:afterUpdate', this._onHotUpdate);
    }
  }

  handleReload = () => {
    window.location.reload();
  };

  handleResetSession = () => {
    try {
      localStorage.clear();
      sessionStorage.clear();
    } catch {
      // ignore
    }
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-slate-900 text-slate-100 flex items-center justify-center p-6 antialiased font-sans">
          <div className="max-w-xl w-full bg-slate-800/90 border border-slate-700 rounded-3xl p-8 shadow-2xl space-y-6">
            <div className="flex items-center space-x-3 text-rose-400">
              <div className="p-3 bg-rose-500/10 border border-rose-500/20 rounded-2xl">
                <AlertTriangle className="w-8 h-8 text-rose-500" />
              </div>
              <div>
                <h2 className="text-xl font-black text-white">Đã xảy ra lỗi giao diện</h2>
                <p className="text-xs text-slate-400 font-medium">Reup Studio Error Boundary</p>
              </div>
            </div>

            <div className="p-4 bg-slate-950/80 border border-slate-800 rounded-2xl overflow-auto max-h-60 text-xs font-mono text-rose-300">
              <p className="font-bold text-rose-400 mb-1">{this.state.error?.toString()}</p>
              {this.state.errorInfo?.componentStack && (
                <pre className="text-[11px] text-slate-400 whitespace-pre-wrap">
                  {this.state.errorInfo.componentStack}
                </pre>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-3 pt-2">
              <button
                onClick={this.handleReload}
                className="flex-1 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold py-3 px-4 rounded-xl flex items-center justify-center gap-2 transition cursor-pointer shadow-lg shadow-blue-500/20"
              >
                <RefreshCw className="w-4 h-4" />
                <span>Tải lại trang</span>
              </button>

              <button
                onClick={this.handleResetSession}
                className="bg-slate-700 hover:bg-slate-600 text-slate-200 text-xs font-bold py-3 px-4 rounded-xl flex items-center justify-center gap-2 transition cursor-pointer"
                title="Xoá session cache bị lỗi và mở lại"
              >
                <Trash2 className="w-4 h-4 text-rose-400" />
                <span>Khởi động lại (Reset Cache)</span>
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
