import { Suspense } from "react";
import { MovementsView } from "./MovementsView";

export default function MovementsPage() {
  return (
    <Suspense>
      <MovementsView />
    </Suspense>
  );
}
