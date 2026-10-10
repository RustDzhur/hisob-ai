import { Suspense } from "react";
import { AccountsView } from "./AccountsView";

export default function AccountsPage() {
  return (
    <Suspense>
      <AccountsView />
    </Suspense>
  );
}
