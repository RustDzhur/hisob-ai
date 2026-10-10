"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { ErrorState, Skeleton } from "@/components/ui";
import { lastOrg, useSession } from "@/lib/session";

// Корень только направляет: вход -> организация -> главная организации.
export default function Home() {
  const router = useRouter();
  const { state, reload } = useSession();

  useEffect(() => {
    if (state.status === "anon") router.replace("/login");
    else if (state.status === "ready") {
      if (state.orgs.length === 0) router.replace("/onboarding");
      else {
        const remembered = state.orgs.find((o) => o.id === lastOrg());
        router.replace(`/o/${(remembered ?? state.orgs[0]).id}`);
      }
    }
  }, [state, router]);

  if (state.status === "error") return <main className="content"><ErrorState error={state.error} onRetry={reload} /></main>;
  return <main className="content"><Skeleton /></main>;
}
