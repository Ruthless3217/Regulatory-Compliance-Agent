"use client";
import * as React from "react";

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** Re-run the loader (e.g. after a mutation or a manual refresh). */
  reload: () => void;
}

/**
 * Small data-loading hook for the console client pages. Re-runs the loader
 * whenever `deps` change (typically the date-range) and exposes a `reload()`
 * for post-mutation refreshes. Ignores results from stale in-flight loads.
 */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: React.DependencyList
): AsyncState<T> {
  const [data, setData] = React.useState<T | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [nonce, setNonce] = React.useState(0);

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    loader()
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // `loader` is intentionally excluded — callers pass an inline closure whose
    // identity changes every render; `deps` + `nonce` drive the reload instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = React.useCallback(() => setNonce((n) => n + 1), []);
  return { data, loading, error, reload };
}
