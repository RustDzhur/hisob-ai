import { Suspense } from "react";
import { CounterpartiesView } from "./CounterpartiesView";

export default function CounterpartiesPage() {
  return (
    <Suspense>
      <CounterpartiesView />
    </Suspense>
  );
}
