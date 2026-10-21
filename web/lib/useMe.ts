"use client";

import { useCallback, useEffect, useState } from "react";
import { auth, type Me } from "./api";

/** Status login + kuota. `refresh()` dipanggil setelah login/logout. */
export function useMe() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setMe(await auth.me());
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  return { me, setMe, loading, refresh };
}
