import { Suspense } from "react";
import { OperationsView } from "./OperationsView";

export default function OperationsPage() {
  return (
    <Suspense>
      <OperationsView />
    </Suspense>
  );
}
