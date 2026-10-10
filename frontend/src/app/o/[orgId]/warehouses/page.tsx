import { Suspense } from "react";
import { WarehousesView } from "./WarehousesView";

export default function WarehousesPage() {
  return (
    <Suspense>
      <WarehousesView />
    </Suspense>
  );
}
