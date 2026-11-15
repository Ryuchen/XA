import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DependencyList,
} from "react";

export function useResource<T>(
  loader: () => Promise<T>,
  dependencies: DependencyList = [],
) {
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const sequence = useRef(0);
  const mounted = useRef(true);
  const [data, setData] = useState<T>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const reload = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true);
    setError(null);
    try {
      const result = await loaderRef.current();
      if (mounted.current && current === sequence.current) {
        setData(result);
        setLoading(false);
      }
      return result;
    } catch (cause) {
      if (mounted.current && current === sequence.current) {
        setError(
          cause instanceof Error ? cause : new Error("读取失败，请重试"),
        );
        setLoading(false);
      }
      return undefined;
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void reload();
    return () => {
      mounted.current = false;
      sequence.current++;
    };
  }, [reload, ...dependencies]);
  return { data, loading, error, reload };
}
export function normalizeList<T = any>(value: unknown): T[] {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== "object") return [];
  const item = value as Record<string, unknown>;
  for (const name of ["list", "items", "results"])
    if (Array.isArray(item[name])) return item[name] as T[];
  return [];
}
export function formatDateTime(value: unknown): string {
  if (!value) return "—";
  const date = new Date(String(value));
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(date);
}
