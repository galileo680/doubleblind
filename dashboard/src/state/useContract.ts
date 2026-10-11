import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { parseContract, type ContractView } from "../lib/contract";

const texts = new Map<string, string>();
const diffs = new Map<string, string>();

interface Loaded<T> {
  value: T | null;
  error: string | null;
}

function useCached(
  cache: Map<string, string>,
  key: string | null,
  load: () => Promise<string>,
): Loaded<string> {
  const [state, setState] = useState<Loaded<string> & { key: string | null }>({
    key: null,
    value: null,
    error: null,
  });
  useEffect(() => {
    if (!key || cache.has(key)) return;
    let cancelled = false;
    load()
      .then((text) => {
        cache.set(key, text);
        if (!cancelled) setState({ key, value: text, error: null });
      })
      .catch((failure: unknown) => {
        if (!cancelled)
          setState({
            key,
            value: null,
            error: failure instanceof Error ? failure.message : String(failure),
          });
      });
    return () => {
      cancelled = true;
    };
  }, [cache, key, load]);
  if (!key) return { value: null, error: null };
  if (cache.has(key)) return { value: cache.get(key) ?? null, error: null };
  return state.key === key ? state : { value: null, error: null };
}

export function useContractText(
  id: string,
  token: string | null,
  version: number | null,
  revision = 0,
) {
  const key = token && version ? `${id}:${version}:${revision}` : null;
  const load = useMemo(
    () => () => api.contract(id, token ?? "", version ?? 1),
    [id, token, version],
  );
  return useCached(texts, key, load);
}

export function useContract(
  id: string,
  token: string | null,
  version: number | null,
  revision = 0,
) {
  const { value, error } = useContractText(id, token, version, revision);
  const contract = useMemo<ContractView | null>(
    () => (value ? parseContract(value) : null),
    [value],
  );
  return { text: value, contract, error };
}

export function useContractDiff(id: string, token: string | null, from: number, to: number) {
  const key = token && from > 0 && to > from ? `${id}:${from}:${to}` : null;
  const load = useMemo(() => () => api.diff(id, token ?? "", from, to), [id, token, from, to]);
  return useCached(diffs, key, load);
}
