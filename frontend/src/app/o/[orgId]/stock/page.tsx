import { Suspense } from "react";
import { StockView } from "./StockView";

export default function StockPage() {
  return (
    <Suspense>
      <StockView />
    </Suspense>
  );
}
