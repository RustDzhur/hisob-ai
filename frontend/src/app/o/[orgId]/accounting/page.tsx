import { Suspense } from "react";
import { EntriesView } from "./EntriesView";

export default function AccountingPage() {
  return (
    <Suspense>
      <EntriesView />
    </Suspense>
  );
}
