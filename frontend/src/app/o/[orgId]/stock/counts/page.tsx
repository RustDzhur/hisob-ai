import { Suspense } from "react";
import { CountsView } from "./CountsView";

export default function CountsPage() {
  return (
    <Suspense>
      <CountsView />
    </Suspense>
  );
}
