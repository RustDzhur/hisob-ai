import { Suspense } from "react";
import { ReservationsView } from "./ReservationsView";

export default function ReservationsPage() {
  return (
    <Suspense>
      <ReservationsView />
    </Suspense>
  );
}
