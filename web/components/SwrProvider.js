"use client";

import { SWRConfig } from "swr";

export default function SwrProvider({ children }) {
  return (
    <SWRConfig
      value={{
        fetcher: (u) => fetch(u).then((r) => r.json()),
        revalidateOnFocus: false,
      }}
    >
      {children}
    </SWRConfig>
  );
}
