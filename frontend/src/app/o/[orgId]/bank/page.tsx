import { Suspense } from "react";
import { BankTransactionsView } from "./BankTransactionsView";

export default function BankPage() {
  return (
    <Suspense>
      <BankTransactionsView />
    </Suspense>
  );
}
